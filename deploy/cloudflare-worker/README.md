# Subarr telemetry receiver (Cloudflare Worker)

The endpoint subarr installs POST to once per 24h. Stores raw payload
+ aggregate counters in KV so the public stats page can read them.

## Setup (one-time, ~5 min)

### 1. Create the KV namespaces

Cloudflare dashboard → **Storage & Databases** → **KV** → **Create namespace**:

- `PINGS`  (stores per-install pings + rate-limit keys)
- `STATS`  (stores aggregate counters for the public dashboard)

Note the namespace IDs.

### 2. Deploy the worker code

Two options:

**Option A — Dashboard Quick Edit (no CLI needed):**

1. Workers & Pages → your worker → **Edit code**
2. Replace contents with `worker.js` from this dir
3. Click **Deploy**

Then bind the KV namespaces:

1. Worker → **Settings** → **Bindings** → **+ Add binding** → **KV namespace**
2. Variable name: `PINGS`, KV namespace: pick the `PINGS` ns
3. Repeat for `STATS`

**Option B — wrangler CLI:**

```bash
npm install -g wrangler
cd deploy/cloudflare-worker
# edit wrangler.toml to fill in the KV namespace IDs
wrangler login
wrangler deploy
```

### 3. Bind custom domain

Worker → **Settings** → **Domains & Routes** → **+ Add** → **Custom Domain**
→ `telemetry.subarr.com` → **Add Domain**.

Cloudflare auto-creates the DNS record + SSL cert. Wait ~30s.

### 4. Test

```bash
curl https://telemetry.subarr.com/v1/health
# → {"status":"ok","service":"subarr-telemetry"}

curl -X POST https://telemetry.subarr.com/v1/ping \
  -H "Content-Type: application/json" \
  -d '{
    "install_id":"a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
    "sent_at": 1779990000,
    "subarr_version":"v1.0.0"
  }'
# → {"ok":true,"received_at":1779990000123}
```

If you POST the same install_id twice within an hour, the second
returns `429 rate_limited`.

## What gets stored

| KV key pattern | Value | TTL |
|---|---|---|
| `ratelimit:<install_id>` | `1` | 1h |
| `ping:<install_id>:<ts>` | raw JSON payload | 90 days |
| `count:total` | int | none (monotonic) |
| `count:date:YYYY-MM-DD` | int | none |
| `count:version:<v>` | int | none |
| `count:subgen:<kind>` | int | none |
| `count:tier:<n>` | int | none |
| `count:bucket:<bucket>` | int | none |

`count:*` keys are what the public stats dashboard reads. Raw `ping:*`
keys exist for inspection + future re-aggregation if we want new
counters from historical data.

## What we don't store

- Client IP (visible to the worker but not persisted)
- User-Agent (debug-only, not persisted)
- Anything not in the documented payload schema

## Updating

Edit `worker.js`, redeploy via dashboard Quick Edit or `wrangler deploy`.
Custom domain + KV bindings persist across deploys.

## Public stats dashboard

(Out of scope for this dir — separate Cloudflare Pages project at
`subarr.com/stats` reads from the `STATS` KV namespace.)
