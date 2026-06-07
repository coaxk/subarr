# subarr deployment templates

Three production-ready `compose.yaml` templates, each representing one
tier on subarr's permission ladder. Pick the one that matches your
trust budget, copy it into your stack dir, fill in the `.env`, and
`docker compose up -d`.

| File | Tier | What subarr can see |
|------|------|---------------------|
| `tier1-no-docker.compose.yaml`     | **1** | Nothing — manual entry of every URL + key |
| `tier2-socket-proxy.compose.yaml`  | **2 (RECOMMENDED)** | Read-only container metadata via proxy; auto-detects URLs + versions |
| `tier3-config-mounts.compose.yaml` | **3** | Tier 2 + your *arr config dirs (read-only) for API-key auto-extract |

---

## How to choose

### Tier 1 — "I don't want subarr touching my docker daemon"

You manually type every integration URL and paste every API key into
the wizard. No docker socket access. Maximum isolation.

Pros: smallest blast radius if subarr is ever compromised.
Cons: more clicks during setup; no auto-detection of new containers.

**Pick this if:** you're security-paranoid OR your *arr stack lives
on a different host than subarr.

### Tier 2 — "Smart defaults, sane security" (recommended)

Subarr talks to a hardened docker-socket-proxy that exposes only the
endpoints needed for read-only metadata: container list, network
list, image info, and ping. The wizard pre-fills integration URLs by
matching container images against a known *arr-services catalogue.
You still paste API keys.

The proxy whitelists endpoints explicitly. Subarr cannot:
- Start, stop, restart, kill, or pause containers
- Exec into any container (no shell-out attack surface)
- Pull or build images
- Mount volumes
- Read container logs
- Inspect secrets, configs, or swarm state

Pros: setup goes from "ten fields per integration" to "click confirm";
massive UX win for new users.
Cons: requires running one additional container.

**Pick this if:** you want the convenience of auto-detect but don't
need fully automatic API-key extraction.

### Tier 3 — "Wizard finishes itself"

Tier 2 + read-only volume mounts of your `*arr` config directories.
Subarr can now read API keys directly from `config.yaml` (Bazarr),
`config.xml` (Sonarr/Radarr), `config.ini` (Tautulli) — no manual
paste needed. Wizard becomes a confirmation screen.

Per-integration opt-in: comment out individual volume mounts in the
template for any service you'd rather paste manually. You don't have
to enable auto-extract for everything.

Pros: zero manual API-key entry; ~90 second wizard end-to-end.
Cons: largest blast radius — if subarr is compromised, attacker can
read every mounted config (which means your *arr API keys).

**Pick this if:** you trust subarr more than the API keys are worth.
Most homelab setups, this is fine.

---

## Network strategy

All three templates ship two networks:

- `subarr-internal` (local) — subarr ↔ subarr-subgen ↔ docker-proxy
  traffic, never exposed
- `media-stack` (external, user-owned) — subarr ↔ your existing *arr
  containers. **Rename this** to whatever your existing network is
  called.

Why two: subarr-internal is tightly scoped, you control it. The
external media-stack is where your existing services already live;
joining it lets subarr reach them by container name
(`http://bazarr:6767`) instead of through host port-forwarding.

If you don't already have a shared bridge network, create one:

```bash
docker network create media-stack
```

Then attach your existing Bazarr/Sonarr/Radarr/etc. containers to it.

If joining a shared network isn't possible (different hosts, host
network mode, etc.), subarr falls back to `host.docker.internal` +
published ports. Discovery still works, URLs just look different.

---

## Switching tiers

The tiers are forward-compatible — moving from Tier 1 → 2 → 3 only
adds containers or volume mounts, never removes them. You can change
your mind any time:

1. Edit your `compose.yaml` to match the next-tier template
2. `docker compose up -d` — subarr picks up the new env vars on next
   boot
3. The Settings → Updates panel will show what's now available
4. Re-run the wizard from Settings → Re-run setup if you want to
   re-detect

---

## Security checklist before going public

- [ ] Don't pin `:latest` — use a specific image tag per the file
- [ ] Set `PUID`/`PGID` to a non-root user (most LinuxServer images
      default to 1000:1000)
- [ ] Set `SUBARR_USER` + `SUBARR_PASS` to enable subarr's built-in
      basic auth. It's OFF by default (unauthenticated API) — fine on a
      trusted LAN, but never expose subarr to the internet without it
      (and/or a reverse proxy with auth — Caddy basicauth, Authelia, etc.).
      Note the API can reach the Docker socket, so an exposed, unauth
      instance is a real risk — keep it LAN-only or authenticated.
- [ ] Pin the docker-socket-proxy image to a tag, not `:latest`
- [ ] If Tier 3, double-check the `:ro` flag is present on each
      config mount. Subarr never needs write access — `:ro` makes
      that enforceable.

---

## What's in `.env`

Copy `.env.example` to `.env` and fill in:

```
TZ=Australia/Sydney        # your timezone
PUID=1000
PGID=1000
MEDIA_ROOT=/mnt/nas/Media  # absolute host path to your library

# Tier 1 only — manual integration config:
BAZARR_URL=http://bazarr:6767
BAZARR_API_KEY=abc123...
# (etc. for sonarr/radarr/tautulli)

# Tier 3 only — host paths to *arr config dirs:
BAZARR_CONFIG=/mnt/configs/bazarr
SONARR_CONFIG=/mnt/configs/sonarr
RADARR_CONFIG=/mnt/configs/radarr
TAUTULLI_CONFIG=/mnt/configs/tautulli
```

For Tier 2 / 3, the `*_URL` and `*_API_KEY` entries are optional —
the wizard fills them in via auto-detect.
