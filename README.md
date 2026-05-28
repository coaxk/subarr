# subarr

The brain that drives subgen.

A peer service in the *arr family. Subarr coordinates subtitle generation
across Bazarr + Sonarr + Radarr + Tautulli + subgen + ollama — figuring out
what's actually missing, what's worth generating, when to run it, and
writing the result back so Bazarr's wanted list actually shrinks.

> Bazarr is the librarian. Subgen is the worker. **Subarr is the brain.**

[![status](https://img.shields.io/badge/status-pre%201.0-violet)](https://github.com/coaxk/subarr)
[![tests](https://img.shields.io/badge/tests-192_passing-22d3ee)](#)
[![license](https://img.shields.io/badge/license-MIT-c8c8cc)](LICENSE)

---

## What it solves

The most-felt pains in the Bazarr + subgen + subtitle-automation space
(documented across r/bazarr, GitHub issues, and TRaSH guide forums):

1. **"Bazarr keeps re-searching subs you already have."**
   Subarr probes your media with ffprobe and knows what's already embedded
   or sidecar'd. Coverage walks suppress the false-positive gap rows that
   make Bazarr re-search forever.

2. **"See your whole library's subtitle coverage at a glance."**
   Gap list across every series + movie, prioritised by Tautulli watch
   history. No native tool does this.

3. **"Don't burn GPU on content I'll never watch."**
   Scheduled coverage walks instead of reactive event storms. You tell
   subarr the rule once; it runs nightly and only queues what matches.

4. **"Know exactly which provider gave me this sub."**
   Provenance ledger records every transcribe job — who submitted, what
   subgen version, completion time, Bazarr scan-disk trigger. Unique to
   subarr in this space.

5. **"Treat embedded subs as first-class."**
   SDH vs forced vs commentary vs full are distinct states, not a binary
   "has subs" flag. Per-track audio language detection too.

---

## Quickstart

The fastest path: pull two images, fill in a `.env`, `docker compose up -d`.

```bash
mkdir -p ~/subarr && cd ~/subarr
curl -O https://raw.githubusercontent.com/coaxk/subarr/main/deploy/templates/tier2-socket-proxy.compose.yaml
curl -O https://raw.githubusercontent.com/coaxk/subarr/main/deploy/templates/.env.example
mv .env.example .env
$EDITOR .env  # fill in TZ, MEDIA_ROOT, your *arr network name
docker compose -f tier2-socket-proxy.compose.yaml up -d
```

Open `http://localhost:9922` and walk through the onboarding wizard.

Three deployment tiers are available — see [`deploy/templates/README.md`](deploy/templates/README.md)
for the trade-offs. **Tier 2 is the recommended default**.

---

## Architecture

Subarr is a **coordinator**, not a transcriber. It owns no GPU code; subgen
does that. State lives in the upstream services (Bazarr/Sonarr/Radarr/Tautulli)
+ Docker + subarr's own SQLite for what subarr itself initiates.

```
 ┌───────────┐       ┌─────────┐       ┌─────────┐
 │  Bazarr   │◄─────►│         │      ►│ subgen  │
 │  Sonarr   │       │         │      / │ (whisper)
 │  Radarr   │◄─────►│ subarr  │◄─────  └─────────┘
 │  Tautulli │       │         │
 │  Plex     │◄─────►│         │       ┌─────────┐
 │  ollama   │◄─────►│         │      ►│ host    │
 └───────────┘       └─────────┘       │ docker  │
                                       └─────────┘
```

| Layer | Tech |
|---|---|
| Backend | Python 3.11+ / FastAPI / uvicorn / httpx / SQLite |
| Frontend | React 18 from CDN + design tokens (no build step) |
| Migrations | Hand-rolled SQL runner; one file per change |
| Discovery | Read-only docker API via [tecnativa/docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy) |
| Storage | Single SQLite file at `/data/subarr.db` |
| Telemetry | Anonymous, opt-out, ~1KB/day; public stats at subarr.dev/stats |

---

## What's in v1.0

| Tab | Function |
|---|---|
| **Home** | Live column-as-stage pipeline (discovered → probing → bazarr-wanted → scanning → written-back) + GPU widget + integration health + next scheduled run + recent activity |
| **Coverage** | Flat, dense gap-list table. Score-gradient sort. Reason chips (no-track / embedded-only / bazarr-wanted / low-score / unmonitored). Bulk select + apply rule + queue |
| **Onboarding** | 10-step wizard that auto-detects your *arr stack via docker-socket-proxy, pre-fills URLs from container metadata, optionally extracts API keys from mounted config files |
| **Rules** | Build / Test / Deploy triad for auto-queue rules (inspired by Profilarr). Dry-run preview before commit |
| **Per-file verdict** | Modal timeline showing every probe, scan, write-back for any video file |
| **Settings** | Integration test buttons with version-echo, telemetry transparency panel, updates panel, theme/locale |

---

## The subgen patch story

Subarr drives subgen through three small patches over upstream
McCloudS/subgen that subarr's orchestration model needs:

1. `POST /batch` — bulk scan with one `scan_id` wrapping N files
2. `GET /queue` — queue introspection (type-tracked, dedup-aware)
3. Per-language `SUBGEN_KWARGS_LANG_XX` env-var overrides

These live in [`coaxk/subarr-subgen`](https://github.com/coaxk/subarr-subgen)
as a patch-stack repo (`patches/*.patch` applied to a vendored upstream
submodule). The maintained image is `ghcr.io/coaxk/subarr-subgen:<tag>`.

**You don't need our patched image** — subarr detects which subgen
you've pointed it at and gracefully degrades when capabilities are
missing. See [Compat mode](#compat-mode) below.

---

## Compat mode

Subarr works with any subgen, not just our patched fork. On startup it
probes `/queue` + `/status` and figures out what's available:

| Subgen build | `/queue` | `/batch` | What works |
|---|---|---|---|
| `ghcr.io/coaxk/subarr-subgen` | ✓ | structured response | Everything |
| `mccloud/subgen` (vanilla) | ✗ | plain text | Coverage, Provenance, scheduling — scan submission shows "needs subarr-subgen" |

The Settings panel shows the detected mode + version so there's never
confusion about which features are active.

---

## Telemetry

Subarr ships with **anonymous telemetry ON by default**. Honest and open:

- ~1KB/day payload
- Public dashboard at subarr.dev/stats (will go live when we publish v1.0)
- Settings panel shows you the **exact JSON** we sent on the most recent
  ping
- One-click opt-out in Settings or during the onboarding wizard

**What's in the payload:**
install ID (random UUID, generated locally), subarr version, Python version,
OS/arch, subgen kind ('subarr-subgen' / 'vanilla' / 'unreachable'), subgen
version, integration booleans (configured y/n, never URLs or keys), library
size bucket (`<100` / `100-1k` / `1k-10k` / `>10k`), scheduler mode, walks/day
rolling average, error counts by exception class, docker tier.

**Never in the payload:** file paths, titles, IPs, hostnames, API keys,
languages, anything user-fingerprintable. Enforced by a regression test.

---

## Authentication

Subarr ships with no built-in auth by default — designed to sit behind
a reverse proxy (Authelia / Caddy basicauth / Traefik forward-auth) for
production. The in-product fallback is HTTP Basic auth via env vars:

```yaml
environment:
  SUBARR_USER: youradmin
  SUBARR_PASS: a-very-long-random-password
```

When both are set, every non-monitoring request requires Basic credentials.
`/api/health` always bypasses for monitoring tools.

**Honest limitations** of basic auth: one global user, no per-user audit
trail, credentials transmitted on every request (use HTTPS via the proxy).
Reverse-proxy auth is the right answer for anything that matters.

---

## Updates

Subarr polls GitHub releases once per 24h for both `coaxk/subarr` and
`coaxk/subarr-subgen`. When a new version is available:

- Soft violet pip on the header version label
- "Update available" tile on the Home dashboard
- Full details in Settings → Updates panel with copy-paste compose
  edit instructions
- Breaking-change banner if the GitHub release flags it

No auto-update. You always run `docker compose pull && up -d` yourself.

---

## Development

```bash
git clone https://github.com/coaxk/subarr
cd subarr
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
PYTHONPATH=src uvicorn subarr.app:app --reload --port 9922
```

Tests:

```bash
PYTHONPATH=src pytest -q
```

Migrations:

```bash
# Add a new migration:
touch src/subarr/migrations/004_my_change.sql
# Write your SQL. See src/subarr/migrations/README.md for conventions.
```

---

## Related

- [Bazarr](https://github.com/morpheus65535/bazarr) — the librarian. Subarr
  reads its wanted list and writes back its scan-disk trigger.
- [McCloudS/subgen](https://github.com/McCloudS/subgen) — the worker.
  Subarr drives it via the patches in
  [`coaxk/subarr-subgen`](https://github.com/coaxk/subarr-subgen).
- [subsyncarr](https://github.com/McCloudS/subsyncarr) — the synchroniser.
  Recommended companion for sync issues subarr doesn't tackle. Mentioned
  in the wizard.

---

## License

MIT. See [LICENSE](LICENSE).

The patched subgen image (`ghcr.io/coaxk/subarr-subgen`) is a derived work
of upstream McCloudS/subgen; see that repo's
[`NOTICE`](https://github.com/coaxk/subarr-subgen/blob/main/NOTICE) for
attribution.
