# Changelog

All notable changes to subarr are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/). Versions follow
[Semantic Versioning](https://semver.org/) — major bumps signal
breaking config changes.

## [Unreleased]

### Added — v1.0 release prep

- **All 6 UI screens** delivered from Claude Design and routed at
  `/home`, `/coverage`, `/onboarding`, `/rules`, `/file-modal`,
  `/settings`. Vanilla-JS legacy UI at `/` coexists during the
  migration.
- **Brand identity locked**: violet primary (`#8b5cf6` — the open slot
  in the *arr family palette), cyan probe-accent (`#22d3ee`), warm
  neutrals starting at `#1a1a1c`. Inter / JetBrains Mono / Space
  Grotesk type stack. Probe-bracket glyph for the wordmark.
- **Subgen capability detection** (`SubgenCapabilities`) — startup
  probe of `/status` + `/queue` so the rest of the app knows whether
  it's talking to our patched build or vanilla. Surfaced via
  `/api/integrations/health` for UI consumption.
- **Compat-mode code-path gating**: when vanilla subgen is detected
  (no `/batch` endpoint), `/api/scan` returns 503 with structured
  `{error: "compat_mode", reason, remedy}`. Completion watcher logs
  a one-time warning + skips queue polling. v1.x adds a file-watch
  fallback so vanilla-subgen users get auto-completion too.
- **Schema migration runner** (`subarr.migrate`) with SQL files in
  `src/subarr/migrations/`. Replaces ad-hoc `init_schema()` calls.
  Each migration runs in a transaction; failure rolls back.
- **Migration `001_baseline.sql`** captures the cumulative v0.x
  schema so v0.x → v1.0 upgrades are no-ops on existing data.
- **Migration `002_update_checks.sql`** for the update-notification
  state cache.
- **Migration `003_telemetry.sql`** for telemetry state.
- **Update notification system** — once-per-24h GitHub release poll
  for both `coaxk/subarr` and `coaxk/subarr-subgen`. `/api/updates`
  returns cached state. Breaking-change flag detected from release
  body. UI surfaces will show header pill + Home tile + Settings
  panel.
- **Docker discovery service** (`subarr.docker_discovery`) — Tier-2
  read-only introspection via tecnativa/docker-socket-proxy (or
  raw `/var/run/docker.sock`). Recognises 8 *arr-stack services
  by image regex + container name. Returns inferred URLs based on
  shared-network detection + published-port fallback. Enables the
  onboarding wizard to pre-fill integration URLs.
- **Tier-3 API-key auto-extract** (`subarr.config_extractor`) —
  4-source resolver chain (subarr env → docker inspect env → mounted
  config file → mounted .env). Parsers for Bazarr YAML / Sonarr +
  Radarr XML / Tautulli INI. Conflict surfacing when sources
  disagree. Per-integration opt-in.
- **Three production-ready compose templates** at `deploy/templates/`:
  tier-1 (no docker access), tier-2 (recommended, read-only proxy),
  tier-3 (opt-in config-mount auto-extract). Each lint-tested with
  `docker compose config`.
- **Telemetry** — anonymous, ON by default, opt-out one-click.
  `/api/telemetry/{state,preview,opt-in,opt-out,send-now}` endpoints.
  Settings panel shows the exact JSON we sent. Public stats at
  subarr.dev/stats (when published).
- **HTTP Basic auth** middleware via `SUBARR_USER` / `SUBARR_PASS`
  env vars. Disabled by default. `/api/health` + `/static/*` bypass
  for monitoring tools.
- **README rewrite** + one-line `install.sh` quickstart with smart
  port detection.
- **Security CI** — bandit + pip-audit + semgrep + trivy workflows
  on PR + weekly schedule. SARIF uploads to GitHub code-scanning UI.

### Changed

- **Coverage gap-list**: authoritative stale-disk via Sonarr file
  paths replaces fragile S<NN>E<NN> filename pattern matching.
  Catches `Part.N` / `Episode_NN` / arbitrary release naming.
  Language-aware: an English sidecar doesn't satisfy a Dutch wanted
  row.
- **Drop "show stale" and "show probe-suppressed" toggles** — gap
  list is now authoritative. If a row appears, it genuinely needs
  work.
- **Static-asset cache-bust** uses container-startup timestamp, not
  just version string, so in-place compose rebuilds bust browser
  caches even when version doesn't change.

### Fixed

- **Bazarr task-name discovery** — runtime-discovered task IDs
  match both `job_id` AND `name` field, hint order prioritises
  `series_full_scan_subtitles` (the real disk-scan task on Bazarr
  1.5.6). Earlier hint list never matched.
- **Cascade synthetic events bubble** — Coverage tree's select-all
  cascades fire `new Event('change', {bubbles: true})` so the
  delegated bulk-counter listener actually updates.
- **Scheduler in-flight skip** — auto-queue rules now consult the
  provenance ledger and skip paths already submitted, preventing
  duplicate scans.

---

## Pre-v1.0 history

See git log on the `main` branch for v0.1.x history. Highlights:

- **v0.1.x (live)** — Manual driver + monitoring (Scan / Coverage /
  Library / Logs / Monitor / Activity / Automation / Settings tabs)
- **Coverage reconciliation engine** with ffprobe-based embedded-sub
  detection
- **Provenance ledger** + completion watcher + Bazarr scan-disk
  write-back
- **Scheduler** with auto-queue rules in dashboard / manual_confirm /
  auto_rules modes
- **LLM enrichment** via Ollama for originalLanguage inference
- **subarr-subgen** patch-stack repo + `v2026.05.3-r1` released to
  GHCR
