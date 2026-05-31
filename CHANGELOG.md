# Changelog

All notable changes to subarr are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/). Versions follow
[Semantic Versioning](https://semver.org/) — major bumps signal
breaking config changes.

## [Unreleased]

Active development on `feat/wire-chrome-dashboard-queue-library` — will fold
into the next minor release.

### Added
- Brand assets: 4-size favicon (16/32/192/512 PNG) + 1200×630 OpenGraph
  share card (#116, #117). Regenerable via
  `python scripts/generate-brand-assets.py`.
- Onboarding auto-detects bind-mounted media paths from
  `/proc/self/mountinfo` (#130). One-click chips on the Paths step.
- Ollama enrichment switched to structured JSON output — schema-constrained
  `iso_code` + `confidence` + `reasoning` instead of free-text parsing (#156).
  SQLite cache migrates idempotently.
- Sidecar basename mismatch detector + auto-rename. Walks the library,
  flags `.srt` files whose name diverged from their video sibling
  (case-mismatch, trailing-tag, stem-typo). New `/api/sidecar/scan` +
  `/api/sidecar/rename` endpoints (#203).
- `release.yml`: tests + frontend drift check run BEFORE GHCR publish so a
  red CI bar halts the image push (#119).

### Fixed
- `release.yml` `type-semver` typo: was producing a literal `:type-semver`
  Docker tag AND blocking `:1.1.0` from being created (#119). v1.1.0 image
  in production until next tag.
- Review page Refresh button felt broken — sub-100ms fetch made the
  spinner flash imperceptibly. Now padded to 350ms + visible "updated Xs ago"
  stamp that resets on click.

### Changed
- Onboarding Step 2 labels — "Container's view of media" framing with
  hints pointing at the right compose.yaml volumes line (#134).

## [1.1.0] — 2026-05-31

Coverage dashboard + the v1.1.1 polish sprint as one release. 26 commits
from the `feat/wire-chrome-dashboard-queue-library` branch.

### Added
- Friendly 4-tile + welcome-card header pattern across Home / Rules /
  Coverage / Settings.
- End-to-end audio-language ground-truth chain: detect → suspect →
  review queue → propagate to Sonarr → bypass subgen skip-list →
  override Library display.
- subarr-subgen v4.3 capability: `audio_language_override` query
  param on POST /batch + capability advertisement on GET /queue.
  subarr feature-detects + degrades on vanilla / v4.2.
- Per-language `SUBGEN_KWARGS_LANG_*` blocks visible in Settings →
  Subgen with hover-tipped explanations for the 13 most-tuned Whisper
  kwargs (#171).
- Per-service ARR path prefix: `sonarr_path_prefix` +
  `radarr_path_prefix` instead of one shared value (#133).
- Library: cascade-select with inherited-checked indicator (#211).
  Search auto-expands category roots (#194). Probe-state indicators
  on the AUDIO / SUBS / LENGTH column (#212).
- 9 onboarding fixes: silent auto-detect failure (#129), container
  hostname suggestion (#137), large-library connection timeout (#138),
  URL field guidance (#139), "wanted" → "missing-subs" copy (#140),
  anticipatory URL prefill (#141), Ollama port disambiguation (#144),
  GPU failure guidance (#145), first-walk fix (#146).

### Fixed
- Empty-string env vars no longer fall through to wrong defaults (#127).
  New `_env_or` helper.
- Library: respect `audio_lang_store` user verifications instead of
  showing stale probe data (#222).
- Coverage caching + background refresh (kills 60-90s loads) (#196).
- Many UX bugs from the live-drive cycle (#197 / #198 / #199 / #200 /
  #201 / #210).

### Changed
- Test suite up to 228 passing (was 192). Test debt from v1.1-O/K/L/M
  cleared (#221).

## [1.0.0] — 2026-05-30 (legacy section preserved below)

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
  subarr.com/stats (when published).
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
