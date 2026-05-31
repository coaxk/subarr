# subarr

**The queue + control layer subgen never had.**

[![release](https://img.shields.io/badge/release-v1.1.0-violet)](https://github.com/coaxk/subarr/releases/tag/v1.1.0)
[![tests](https://img.shields.io/badge/tests-228_passing-22d3ee)](#)
[![image](https://img.shields.io/badge/image-ghcr.io%2Fcoaxk%2Fsubarr-2596be)](https://github.com/coaxk/subarr/pkgs/container/subarr)
[![license](https://img.shields.io/badge/license-MIT-c8c8cc)](LICENSE)

> Subgen has no queue. No reorder. No pause. No "transcribe these three episodes from two shows" — just blind, manual triggers and a worker that runs until you kill it.
>
> **Subarr fixes that.**

---

## Why subarr exists

Three pains that drove this product into existence. Each one is a thing subgen straight-up cannot do today.

### 1. Subgen has zero queue control — subarr has supreme queue flexibility

Pick **single files**. Pick **batches across shows**. Pick **a full season**. Pick **multiple seasons at once**. Then **promote**, **demote**, **pause**, **remove**, or **reorder** anything in the queue — without killing the worker, without restarting the container, without losing what's already in flight.

Subgen has none of this. You point it at a directory, you hope. Subarr gives you a build-the-queue-you-want UI with a live activity panel watching each job.

> 📸 *(screenshot: Library tree → checkbox three episodes across two shows → queue → drag-reorder)*

### 2. Subgen has no brain — subarr ranks what's worth your GPU

Subgen transcribes whatever you point it at, whenever you point it at it. That's a lot of GPU on shows you stopped watching three months ago.

Subarr ranks your queue with **Tautulli watch history × Sonarr/Radarr metadata × language gap coverage**. Schedule it (**interval / daily / weekly**) and the right files keep getting transcribed automatically — with **preview-before-commit** and **manual-confirm** modes if you'd rather review the next batch before it fires.

> 📸 *(screenshot: Settings → Schedule + Home "Next run" panel showing ranked queue building itself)*

### 3. You find subtitle gaps when Bazarr fails — subarr shows you before

Bazarr's wanted list is a *consequence list*: it tells you what's broken, not what's missing. Subarr's **Coverage** view is the gap list itself — dense table, per-show per-language, with **stale-disk overlay** so you know what's on disk vs. what Bazarr thinks is on disk.

When you see a gap, you queue the fix in one click. The provenance ledger keeps the receipt.

> 📸 *(screenshot: Coverage tree with dense gap list + stale-disk overlay)*

---

## Install

Two paths. Pick the one that matches how you run your homelab.

### Docker Compose (recommended for homelabs)

Drop this `compose.yaml` next to a `.env`:

```yaml
services:
  subarr:
    image: ghcr.io/coaxk/subarr:v1.0.0-rc.1
    container_name: subarr
    restart: unless-stopped
    ports:
      - "9922:9922"
    environment:
      TZ: ${TZ}
      # Optional basic auth — leave both unset to skip
      # SUBARR_USER: admin
      # SUBARR_PASS: change-me-long-random
    volumes:
      - ./config:/config
      - ${MEDIA_ROOT}:/media:ro
    networks:
      - arr-net
    logging:
      driver: json-file
      options:
        max-size: 10m
        max-file: "3"

networks:
  arr-net:
    external: true
    name: ${ARR_NETWORK:-arr-net}
```

Minimal `.env`:

```bash
TZ=Australia/Sydney
MEDIA_ROOT=/mnt/nas/Media
ARR_NETWORK=arr-net          # match whatever network your Bazarr/Sonarr already use
```

Then:

```bash
docker compose up -d
```

Open `http://localhost:9922`. The 10-step onboarding wizard auto-detects your existing *arr stack if you bind the docker socket via the [Tier-2 socket-proxy template](deploy/templates/tier2-socket-proxy.compose.yaml) — recommended; full deployment matrix in [`deploy/templates/README.md`](deploy/templates/README.md).

### One-line bootstrap (for trying it fast)

```bash
curl -fsSL https://github.com/coaxk/subarr/raw/main/install.sh | bash
```

Pulls the same image, creates a sensible default config dir, prints the dashboard URL.

### Minimum compose stub (if you'd rather hand-roll)

If you don't want the socket-proxy and prefer to point subarr at your
*arr stack manually, this is the smallest possible compose that runs:

```yaml
services:
  subarr:
    image: ghcr.io/coaxk/subarr:latest
    container_name: subarr
    restart: unless-stopped
    networks: [media-stack]      # same network as Bazarr/Sonarr/Radarr
    ports: ["9922:9922"]
    environment:
      TZ: Australia/Sydney
      SUBARR_MEDIA_ROOT: /media/library
      SUBGEN_URL: http://subgen:9000
      # URLs filled by the wizard, or set them here to skip the wizard step
      BAZARR_URL:   http://bazarr:6767
      SONARR_URL:   http://sonarr:8989
      RADARR_URL:   http://radarr:7878
      TAUTULLI_URL: http://tautulli:8181
    volumes:
      # Host path (LEFT) is where your media lives on the host machine.
      # Container path (RIGHT) MUST match SUBARR_MEDIA_ROOT above —
      # /media/library is the wizard's default. Mount read-only when
      # subarr is the only writer of sidecars (it is, by default).
      - /mnt/nas/Media:/media/library:ro
      - ./data:/data            # subarr's SQLite + provenance ledger
networks:
  media-stack:
    external: true              # change to false if you want a new network
```

This skips auto-detect; the wizard still walks you through the rest.
For socket-proxy-backed auto-detect, use the Tier 2 template above.

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
| Telemetry | Anonymous, opt-out, ~1KB/day; public stats at subarr.com/stats |

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
- Public dashboard at subarr.com/stats (will go live when we publish v1.0)
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

**Note for Pi-hole users**: many privacy-conscious Pi-hole regex
blocklists deny anything matching `*telemetry*` by default. We use
the literal subdomain `telemetry.subarr.com` because hiding behind a
misleading name (e.g. `stats.subarr.com`) would be the opposite of
honest. If you actively want telemetry off, *don't allow it*. If you
want to send it, allow `subarr.com` in your Pi-hole and the regex
deny will no longer apply.

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

## Networking: how subarr finds your *arr stack

Subarr's integrations (Sonarr, Radarr, Bazarr, Tautulli, subgen, Plex,
Ollama) reach those services via the URLs you provide in the wizard or in
your `.env`. There are two common topologies:

**1. Shared docker network (recommended).** Add subarr to the same docker
network as your *arr stack. Then you can address each service by its
container name on the default arr ports:

```yaml
networks:
  - safe-bridge       # or whatever your *arr stack already uses

# .env
SONARR_URL=http://sonarr:8989
RADARR_URL=http://radarr:7878
BAZARR_URL=http://bazarr:6767
SUBGEN_URL=http://subgen:9000
TAUTULLI_URL=http://tautulli:8181
PLEX_URL=http://plex:32400
```

This is what `docker-compose.yaml` in `deploy/templates/` ships with.
DNS resolves container names within the network, so no IP addresses
get baked in.

**2. Bypass: subarr on host network or different stack.** If subarr is
deployed standalone (no shared network with the *arr stack), reach each
service by host IP + published port:

```env
SONARR_URL=http://192.168.1.10:8989
RADARR_URL=http://192.168.1.10:7878
BAZARR_URL=http://192.168.1.10:6767
SUBGEN_URL=http://192.168.1.10:9000
```

Common gotcha — **`localhost` from inside a container points at the
container, not the host.** Use the host's LAN IP (or
`host.docker.internal` on Docker Desktop) instead of `localhost`.

The onboarding wizard's "Test connection" button validates each URL
against the live service before you commit it to settings; if it fails
the chip stays red with the actual httpx error so you can fix the URL
without restarting subarr.

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
