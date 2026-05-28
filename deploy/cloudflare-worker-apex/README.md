# Subarr apex redirect (Cloudflare Worker)

Returns a 301 redirect from `subarr.com` (and `www.subarr.com`) to the
GitHub repo's README. The v1.0 landing strategy — costs nothing to
maintain, sends visitors to the source of truth, and stays in sync
with the project automatically.

The `telemetry.subarr.com` worker is separate (see
[`../cloudflare-worker/`](../cloudflare-worker/)) and is unaffected.

## Setup (one-time, ~3 min)

### Option A — Dashboard Quick Edit (no CLI)

1. Cloudflare dashboard → **Workers & Pages** → **Create application** →
   **Create Worker**
2. Name: `subarr-apex-redirect`
3. **Deploy** the placeholder, then **Edit code** → replace contents
   with [`worker.js`](worker.js) → **Save and deploy**
4. Worker → **Settings** → **Domains & Routes** → **+ Add** → **Route**:
   - Route: `subarr.com/*`
   - Zone: `subarr.com`
5. Repeat step 4 for `www.subarr.com/*` if you want the `www.` variant
   too

### Option B — wrangler CLI

```bash
npm install -g wrangler
cd deploy/cloudflare-worker-apex
wrangler login
wrangler deploy
```

Then bind the routes in the dashboard as in step 4 above. (Wrangler
can configure routes via `wrangler.toml` `[[routes]]` too, but the
dashboard route picker is less error-prone for apex setup.)

## Verify

```bash
curl -I https://subarr.com/
# Should show:  HTTP/2 301
#               location: https://github.com/coaxk/subarr

curl -I https://subarr.com/robots.txt
# HTTP/2 200, Disallow: / (so crawlers don't index the redirect)

curl -I https://subarr.com/favicon.ico
# HTTP/2 204 (silences favicon 404 noise in worker analytics)
```

## When to replace this

Drop this worker and swap in a real landing site (Cloudflare Pages
hosting an `index.html`, or a richer worker that serves marketing
copy) when:

- you want analytics beyond Worker request counts
- you want anything bigger than a one-line pitch on the apex
- you want `subarr.com/stats` to render the public telemetry counters
  (which the telemetry worker is already writing to KV)

Until then, this 301 is the simplest correct answer.

## What it does NOT do

- Does not serve the subarr app itself — that's deployed by users on
  their own homelab and runs at e.g. `http://localhost:9922`
- Does not gate or proxy telemetry — `telemetry.subarr.com` is its own
  worker on its own subdomain
- Does not collect any visitor data — no cookies, no analytics
  scripts, no fingerprinting
