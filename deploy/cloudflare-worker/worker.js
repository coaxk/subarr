/**
 * subarr telemetry receiver.
 *
 * Accepts one-per-day pings from subarr installs. Stores raw payload
 * + extracted aggregates in KV for the public stats dashboard at
 * subarr.com/stats to read.
 *
 * Endpoint:  POST telemetry.subarr.com/v1/ping
 * Health:    GET  telemetry.subarr.com/v1/health
 *
 * Validation:
 *   - Method must be POST for /v1/ping
 *   - Content-Type must be application/json
 *   - Payload must parse as JSON
 *   - Required fields: install_id, sent_at, subarr_version
 *   - Body size capped at 8KB (real payloads are ~1KB)
 *
 * Rate limiting:
 *   - install_id can ping at most once per hour (24h is the subarr-side
 *     cadence; 1h here gives slack for manual "send now" testing)
 *   - Enforced via KV TTL on the install_id key
 *
 * What we DON'T store:
 *   - Client IP (we have it from the request but don't write it)
 *   - User-Agent (logged for one tick of debugging, not persisted)
 *   - Geographic info beyond what we'd compute from country code
 *     for aggregate counts only
 *
 * Required Worker bindings (configure in wrangler.toml or Cloudflare UI):
 *   - PINGS  (KV namespace) — stores per-install last-payload
 *   - STATS  (KV namespace) — stores aggregate counters
 *
 * Deploy:
 *   wrangler deploy
 * or paste this file in the Cloudflare dashboard Quick Edit tab.
 */

const MAX_BODY_BYTES = 8 * 1024;        // 8KB ceiling
const RATE_LIMIT_SECONDS = 60 * 60;     // 1 hour minimum between pings per install
const PING_RETENTION_DAYS = 90;         // keep raw pings 90 days

export default {
  /**
   * @param {Request} request
   * @param {{PINGS: KVNamespace, STATS: KVNamespace}} env
   * @param {ExecutionContext} ctx
   */
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // ── CORS preflight (the subarr Settings panel may want to display
    //     the public stats — same-origin in production, but be permissive
    //     during dev). Production deploy puts subarr.com/stats and the
    //     receiver on the same root domain so this is mostly defensive.
    if (request.method === 'OPTIONS') {
      return cors(new Response(null, { status: 204 }));
    }

    if (url.pathname === '/v1/health') {
      return cors(json({ status: 'ok', service: 'subarr-telemetry' }));
    }

    if (url.pathname === '/v1/ping') {
      return cors(await handlePing(request, env, ctx));
    }

    return cors(new Response('not found', { status: 404 }));
  },
};

async function handlePing(request, env, ctx) {
  if (request.method !== 'POST') {
    return json({ error: 'method_not_allowed', expected: 'POST' }, 405);
  }
  const contentType = request.headers.get('content-type') || '';
  if (!contentType.toLowerCase().includes('application/json')) {
    return json({ error: 'unsupported_media_type', expected: 'application/json' }, 415);
  }

  const raw = await request.text();
  if (raw.length > MAX_BODY_BYTES) {
    return json({ error: 'payload_too_large', max_bytes: MAX_BODY_BYTES }, 413);
  }

  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    return json({ error: 'invalid_json' }, 400);
  }

  // ── Required-field validation
  const required = ['install_id', 'sent_at', 'subarr_version'];
  for (const f of required) {
    if (!(f in payload)) {
      return json({ error: 'missing_field', field: f }, 400);
    }
  }
  const installId = String(payload.install_id);
  if (!/^[a-f0-9]{32}$/i.test(installId)) {
    return json({ error: 'invalid_install_id' }, 400);
  }

  // ── Rate limit per install_id
  const rateKey = `ratelimit:${installId}`;
  const lastSeen = await env.PINGS.get(rateKey);
  if (lastSeen) {
    return json({
      error: 'rate_limited',
      retry_after_seconds: RATE_LIMIT_SECONDS,
    }, 429);
  }
  // Set rate-limit TTL FIRST so a flood of pings can't slip in via
  // the gap between the read above and the store below.
  ctx.waitUntil(env.PINGS.put(rateKey, '1', { expirationTtl: RATE_LIMIT_SECONDS }));

  // ── Persist payload (raw, for inspection + future re-aggregation)
  const pingKey = `ping:${installId}:${Date.now()}`;
  ctx.waitUntil(env.PINGS.put(pingKey, raw, {
    expirationTtl: PING_RETENTION_DAYS * 24 * 60 * 60,
    metadata: {
      received_at: Date.now(),
      cf_country: request.cf?.country || 'unknown',
      subarr_version: payload.subarr_version,
    },
  }));

  // ── Bump aggregate counters (best-effort; failures don't reject the ping)
  ctx.waitUntil(bumpAggregates(env.STATS, payload).catch(() => {}));

  return json({ ok: true, received_at: Date.now() });
}

/**
 * Increment aggregate counters in KV. Used by the public stats page.
 * Each counter is just a single int stored under a deterministic key.
 */
async function bumpAggregates(stats, payload) {
  const today = new Date().toISOString().slice(0, 10);     // YYYY-MM-DD
  const keys = [
    `count:total`,
    `count:date:${today}`,
    `count:version:${payload.subarr_version}`,
    `count:subgen:${payload.subgen_kind || 'unknown'}`,
    `count:tier:${payload.docker_tier ?? 'unknown'}`,
    `count:bucket:${payload.library_bucket || 'unknown'}`,
  ];
  await Promise.all(keys.map(async (key) => {
    const cur = parseInt((await stats.get(key)) || '0', 10);
    await stats.put(key, String(cur + 1));
  }));
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

function cors(response) {
  const h = new Headers(response.headers);
  h.set('access-control-allow-origin', '*');
  h.set('access-control-allow-methods', 'POST, GET, OPTIONS');
  h.set('access-control-allow-headers', 'content-type');
  h.set('access-control-max-age', '86400');
  return new Response(response.body, { status: response.status, headers: h });
}
