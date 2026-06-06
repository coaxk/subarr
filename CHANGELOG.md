# Changelog

All notable changes to subarr are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/). Versions follow
[Semantic Versioning](https://semver.org/) — major bumps signal
breaking config changes.

## [1.2.0] - 2026-06-07

### Added
- **Tuning Lab — find the Whisper settings that actually win, on your hardware.**
  An in-app config arena: pick a file, choose recipes to compare, and subarr
  runs each against the live subgen model and lets a tournament judge rank them
  objectively. It auto-samples up to 3 short strata clips per file (dialogue, a
  speech→silence edge, a quiet stretch) and requires a recipe to win *across*
  clips, not on one. A per-language "herd" view aggregates results so a
  dependable default emerges per language; bulk multi-file sweeps gather data
  fast; everything runs over subgen with nothing written to your library. (#131)
- **Audio-language verification — subarr *listens* and tells you the truth
  about a track.** This is something the *arr metadata chain structurally
  can't do: Sonarr/Radarr tag a show's language and everyone downstream
  parrots it, even when it's wrong. The Tuning Lab's robust multi-chunk Whisper
  detection verifies the *spoken* language by ear, and reads the per-chunk
  agreement to tell three real situations apart:
    - a **mislabeled track** (file tagged Danish, audio unanimously Dutch) →
      flags it and offers a one-click correction that flows back into coverage;
    - a **bilingual file** (English detectives + Serbian crooks; or JP/KO) →
      detected as multiple languages and flagged, instead of being mis-collapsed
      to whichever language a chunk happened to land on;
    - **Whisper unsure** → falls back to the known tag rather than guessing.
  A 🎧 listen-and-confirm action (audio player + on-demand detection) settles
  any case in seconds; confirmations persist as ground truth that coverage and
  future sweeps inherit. **Multi-track files** (an original plus a dub) are
  detected too: each audio track is swept separately, labeled by its own
  language, so the Tuning Lab gives per-track recommendations instead of only
  the default track. An **Audio language issues** panel collects every flagged
  file in one place for review.
- **Library-wide audio-language scan.** A one-click **Scan library** runs that
  same listening pass over your whole library — not just files you happened to
  sweep — so subarr can surface mislabels, bilingual tracks, and multi-track
  files proactively. The scan is opt-in, throttled to a background trickle, and
  GPU-polite: it pauses automatically while live Tuning Lab sweeps run and
  resumes on its own, skipping files it already checked (resumable across
  restarts). Findings flow into the same Audio language issues panel, and once
  you confirm a file it drops out for good. (#155)
- **In-app integration credential editing.** Add or change Bazarr/Sonarr/
  Radarr/Tautulli URLs + API keys and the Plex token from Settings, with
  test-connection and live apply — no env edit or restart. Env-set fields stay
  authoritative and read-only. (#75)
- **Push-based subgen completion.** subarr consumes subgen's
  `WEBHOOK_URL_COMPLETED` as an alternative to polling `/queue` (polling stays
  as the fallback). (#87)
- **Series-level audio-language intent inherits to new episodes** — declare a
  series' language once and new episodes resolve as verified during the next
  coverage build. (#69)
- **Language tags as flag icons** across coverage and the Tuning Lab (bundled
  SVGs, no external requests; decoration only — the tooltip names the
  *language*). (#147)
- **Global recipe leaderboard.** The per-language herd rolled up into one
  overall ranking — scored by the *mean of per-language means* so each language
  counts equally and a heavily-swept/easy language can't skew the result.
  Medals for the top three, a confidence signal, and an expandable per-language
  breakdown; recipes need data across at least three languages to earn a rank.
  (#146)

### Changed
- **Performance & best-practices pass.** Responses are gzip-compressed; static
  vendor/flag/favicon assets get a one-week revalidated cache while
  non-hashed bundles + HTML stay `no-cache` (fresh UI the instant it changes);
  a Content-Security-Policy and the standard hardening headers
  (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
  `Permissions-Policy`) are sent on every response; and `/favicon.ico` is
  served directly. (#138)
- **Coverage refresh is debounced + parallelized** — bursts of completion/scan
  events coalesce to one refresh, and the per-series subtitle scans run with
  bounded concurrency instead of one-at-a-time. (#104)
- Forced-only embedded-English files are gated on subgen's runtime
  `IGNORE_FORCED_SUBTITLES` capability, so a forced-only gap is only presented
  as actionable when the connected subgen will actually fill it. (#79)
- Tuning Lab + coverage lists cap their height and scroll in place instead of
  ballooning the page.
- **Age-based retention for tuning-lab sweeps.** The append-only `arena_runs`
  table is pruned on boot to `SUBARR_ARENA_RETENTION_DAYS` (default 30; 0
  disables) so it can't grow unbounded on long-running installs. (#136)

### Fixed
- **ISO language-code variants are normalized in coverage detection.** A present
  `.ger.srt`/`.deu.srt` sidecar now satisfies a `de` target (and `.eng.srt` an
  `en` target) instead of raising a phantom gap; the Bazarr-blind mislabel check
  also catches `eng`/`en-US`, not just bare `en`. (#118)
- **Icelandic is selectable** in the audio-language picker (it was in the
  language map but missing from the dropdown).
- **Telemetry no longer reports the ollama integration "configured" on ~100%
  of installs** — it now gates on real reachability. (#119)
- The main queue no longer counts Tuning Lab sweeps (they run through subgen's
  `/asr` and were inflating the sidebar count against an empty page).
- The favicon ships its 's' as an outlined path, so it renders correctly in
  icon pipelines instead of depending on a font.
- Multi-track fan-out sweeps now label their herd source as `track` (the
  language came from the track's tag) rather than `user`, so track-derived and
  user-pinned languages are distinguishable.

## [1.1.0] - 2026-06-04

### Added
- **Speech-aware audio (silero VAD).** The audio-review player now lands its
  clip on actual dialogue instead of a fixed 5-second window that hit silence
  or intro music most of the time. silero voice-activity detection picks a
  ~12s speech window; a "🎙 speech-detected" badge shows when it's active.
  Opt-in: a "Speech detection" onboarding step and a Settings → System
  "Speech-aware audio" card enable it and pull a ~2 MB model. When off or
  undownloaded it falls back cleanly to the previous silencedetect behaviour.
  The runtime (onnxruntime, no PyTorch) ships in the image; only the model is
  pulled on demand. (#110, #111)
- **Config-persistence layer.** UI settings now survive a container restart,
  with precedence env > persisted-file > built-in default (env stays
  authoritative). (#112)
- **Deterministic subtitle readability linter** (CPS/CPL/line-count/duration/
  overlap), used as a capped secondary signal in the tournament rubric.
  (#92, #108)
- **Whisper-tuning tournament — judging engine + reference-free QE judges**
  (hallucination / looping / canned-phrase / coverage / cross-config
  consensus) + a Tier-B validation harness. Internal foundation this release,
  validated against professional-reference accuracy; surfaces as a user-facing
  tuning lab in v1.2. See `docs/research/tournament-validation.md`.
  (#65, #120, #121, #122)
- **Throttled library-backfill selection core** (opt-in foundation for draining
  the coverage-gap backlog at a target queue depth). (#116)
- `data-testid` capture hooks across the UI for scripted capture / future e2e.
  (#81)

### Changed
- Audio-review clips are now ~12s (was 5s) — long enough to reliably hear
  dialogue. (#110)

### Notes
- New image dependency: the speech-detection runtime (onnxruntime + numpy,
  ~65 MB, no PyTorch) is baked into the image; it's inert until you opt in and
  pull the model.

## [1.0.2] - 2026-06-03

### Fixed
- Coverage refresh no longer freezes the UI. A quadratic loop in the
  bazarr-blind synthetic-rows pass (O(series × episodes)) blocked the event
  loop for 15–20s mid-refresh on large libraries; it's now O(episodes) (#93).
- Multi-episode disc images (`.iso`) no longer sit stuck in the "Analyzing"
  bucket forever. They're disqualified to a distinct `unsupported` state and
  surfaced in "Couldn't analyze" instead (#96, #62).

### Changed
- Dashboard polish (#97–#100): the transcribing card now shows active vs
  queued with a **live per-job progress bar**; the top panels are reordered
  (transcribing · bazarr-wanted · discovered · written-back · probing); the
  GPU widget gains a utilisation graph + a VRAM bar; recent activity is
  tightened (no inner scroll).

### Added
- Header "update available" pill + live running-version label (#78).
- Architecture data-flow diagram + a "how it runs" note in the README (#91),
  and a full subgen surface-audit reference doc (#85).

## [1.0.0] - 2026-06-02

First public release.

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
