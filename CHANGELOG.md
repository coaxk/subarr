# Changelog

All notable changes to subarr are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/). Versions follow
[Semantic Versioning](https://semver.org/) — major bumps signal
breaking config changes.

## [Unreleased]

### Security
- **No internal error detail leaks to API clients (#261).** Endpoints no longer
  return raw exception text; a shared `safe_error()` maps failures to a constant,
  leak-free message by type/HTTP-status (e.g. "authentication failed", "could
  not connect", "the upstream service returned a server error") while the full
  detail goes only to the server log. Operators still see *why* something failed,
  without exposing paths/hostnames/stack info. Clears the 19 CodeQL
  `py/stack-trace-exposure` findings.
- **Forced authentication (#238).** subarr now requires a login by default,
  matching modern Sonarr/Radarr. A fresh install (or the first launch after
  upgrading from a no-auth version) shows a one-time setup screen to create an
  admin account; thereafter a login page + session cookie. **Existing installs
  that already set `SUBARR_USER`/`SUBARR_PASS` or `SUBARR_API_KEY` are NOT
  forced into setup** — those keep working, so automation survives the upgrade.
  - **Recovery (no DB surgery):** env override (`SUBARR_USER`/`SUBARR_PASS`),
    `SUBARR_AUTH_RESET=1` (clear → setup), or
    `docker exec subarr python -m subarr.cli reset-auth` / `set-password`.
  - **Proxy users:** `SUBARR_AUTH_DISABLED=1` delegates auth to your reverse
    proxy (Authelia/Caddy/Traefik) — no double login; a non-matching upstream
    Basic header is ignored, not rejected.
  - **Sessions:** `SUBARR_SESSION_SECRET` persists logins across restarts
    (unset = ephemeral, re-login after restart — never a lockout);
    `SUBARR_COOKIE_SAMESITE=none` for cross-site dashboard iframes.
  - Hardening: pbkdf2 password hashing, session rotation on login (anti
    session-fixation), generic login errors (no user enumeration),
    `secrets.compare_digest` throughout.

### Added
- **Login brute-force throttle (#260).** Failed sign-ins are rate-limited per
  client IP (sliding window, default 5 / 300s, then a short wait — never a
  permanent lockout). `SUBARR_TRUSTED_PROXIES` keys the limit on the real client
  IP behind a reverse proxy (only that hop's `X-Forwarded-For` is trusted);
  `SUBARR_LOGIN_ALLOWLIST` exempts trusted ranges entirely. Both default empty;
  effective values shown read-only under Settings → Login security.
- **Managed API keys (#259).** Mint named API keys in **Settings → API keys** for
  scripts and integrations — beyond the single env `SUBARR_API_KEY`. Each key has
  full access, is shown once at creation (stored only as a SHA-256 hash), is sent
  as `X-API-Key`/`?apikey=`, can be revoked instantly, and shows a last-used time
  so stale keys are obvious.

### Fixed
- **Logins now survive restarts and reloads.** The session-signing secret is
  persisted in the database instead of being regenerated per boot, so a
  container restart/update no longer silently logs everyone out. `SUBARR_SESSION_SECRET`
  remains an optional explicit override. (#238 follow-up.)
- **Graceful session expiry.** A global guard turns any `/api/*` 401 into a
  visible "session expired" notice + redirect to login (with `?next=`), instead
  of silent dead clicks. The login page gained a subarr logo and a "Forgot your
  password?" panel with the CLI/env recovery steps.
- **Env-configured installs no longer forced into the onboarding wizard (#262).**
  The root route only sends you to the first-run wizard when the install is
  genuinely unconfigured. An install set up via env vars (the common arr-stack
  pattern) lands on the dashboard instead of a blank wizard after login.
  Relatedly, the wizard now **pre-fills from live settings** (secrets masked) so
  an explicit "Re-run setup" reflects current config, and "Test connection"
  falls back to the stored credential when a masked field is left untouched.

## [1.6.0] - 2026-06-14

Headline: subarr now configures Whisper for your hardware. No migrations; no
config changes for existing installs.

### Added
- **Guided subgen setup (#231).** A detect-guide-apply flow in the onboarding
  wizard and a new Settings → Subgen tuning panel. It detects the GPU (own
  nvidia-smi, falling back to a Docker-runtime probe, then a paste-the-output
  manual path), recommends a Whisper model from VRAM, and *derives* the
  compute type from compute capability (float16 on Volta+, int8 on older
  cards, int8_float16 when VRAM is tight) with the reasoning shown. Delivers
  the config either as a copy-paste compose/env block (any subgen) or applied
  live via the subarr-subgen runtime endpoint. Pairs with subarr-subgen r9,
  which bakes the tuned kwargs + strongpad regroup, adds an entrypoint
  device-guard, and exposes the runtime-config endpoint.
- **No-auth warning banner (#238).** subarr ships with no authentication by
  default; the dashboard now warns (dismissible) when none is configured, so
  a default install is an informed choice rather than a silent exposure. New
  unauthenticated `GET /api/auth-status` backs it (reports only the boolean,
  no secret).
- **Aftercare score breakdown.** Hover a flagged subtitle's composite score to
  see the contributing signals (looping, hallucination, ad/boilerplate, sync
  overrun) and readability counts.
- Two new aftercare quality signals shipped in 1.5.3 (ad/boilerplate detection
  in edge cues, sync-overrun vs media duration) are surfaced here.

### Fixed
- **Log injection (CWE-117, #239).** User-supplied strings (subgen webhook
  fields, canonical paths) are scrubbed of control characters before logging.
- **Dev/locally-built images no longer nag a backward update (#233).** A
  `dev-<sha>` subgen tag is treated as unreleased rather than compared against
  release tags.
- Tautulli/Ollama versions now show in Settings; the Updates page compares
  versions v-prefix-insensitively (no more `1.6.0 → v1.6.0` phantom upgrade).

### Security / ops
- Hardened compose example (`cap_drop: ALL`, no-new-privileges), documented
  Swagger/OpenAPI at `/docs`, CodeQL added as a gate, and a daily
  security-drift check that fails on any open HIGH code-scanning alert.

## [1.5.4] - 2026-06-13

A first-run polish release ahead of wider launch. No migrations, no config
changes.

### Fixed
- **No more phantom "update available" on a current install.** subarr-subgen
  uses date-style release tags while its internal patch version is separate;
  the update check compared the two and always disagreed, so a fully
  up-to-date install was told to update. It now compares like for like and
  only flags a real update.
- **The header update badge now names what has the update** (subarr vs the
  companion subgen image) instead of an unlabeled "Update".
- **Tautulli and Ollama now show their versions** in the Settings
  integrations grid; fixed a doubled "v" on the Tautulli version.
- **The Updates page shows the exact upgrade command** for whatever has an
  update.

### Docs
- README install section now calls out the only two choices that matter
  (which subgen image, and GPU passthrough); everything else the first-run
  wizard handles.

## [1.5.3] - 2026-06-12

### Added
- **"What you're missing" update card on the dashboard (#203).** When your
  install is behind, the Overview shows the releases you've missed by title
  (not just "an update exists"), dismissable per-version. Reads the GitHub
  releases Atom feed; no API key needed.
- **Find a better config (#165).** Flagged aftercare jobs now have a one-click
  action that opens the Tuning Lab pre-loaded with that exact file and its
  detected language — instead of blind-requeueing the same configuration that
  produced the bad subtitle.
- **Two new subtitle quality signals (#216, first slice).** Aftercare now
  detects ad/boilerplate text in a subtitle's opening/closing cues
  ("downloaded from...", VIP-ad blocks, ripper credits — in a generated sub
  these are hallucinations) and sync overrun (cues ending well past the
  file's actual duration, meaning the subtitle was made for a different cut).
  Both flag for review with their own chips. Neither changes sweep scoring.
- **Single-process guard (#204).** Two subarr processes sharing one database
  corrupt it slowly and confusingly. Boot now takes a lock file next to the
  database and surfaces a loud Health warning if another instance holds it.

### Fixed
- **Unhandled request errors now reach crash telemetry (#199)** instead of
  only background-loop crashes, so 500s are visible on the Health page.
- **Deep-link query parameters survive page redirects.** `/arena?path=...`
  (and every screen route) previously dropped its query string.

### Maintenance
- **Database retention (#197).** Scan history, error events, resolved
  approval walks, and year-old aftercare results are now pruned at boot.
  Protective rules: pending approvals and flagged-but-unreviewed aftercare
  rows are never pruned, regardless of age.
- Top-level RELEASING.md + a full operations runbook for the telemetry
  worker (#205).

## [1.5.2] - 2026-06-12

### Added
- **Movie coverage (Radarr).** Movies missing a subtitle now appear in
  Coverage/Gaps. Previously movies reached Coverage only through Bazarr's
  monitored-wanted list, so any movie that was unmonitored in Bazarr was
  invisible — a Radarr-heavy user could see zero of their movies. Every
  Radarr movie with a file that's missing English coverage is now surfaced
  and runs the exact same verification funnel as TV (ffprobe, embedded-sub
  detection, the calibrated audio-language check, default-track mismatch,
  scoring). The Coverage tree gained a "TV Shows" section header to match
  the existing "Movies" one.
- **Retention/persistence signals in telemetry** (opt-out as always):
  `install_age_days` and `data_persistent`.

### Fixed
- **Loud warning when `/data` isn't a persistent volume.** Running without a
  volume for `/data` silently wiped every verification on each recreate. Boot
  now detects an ephemeral `/data` and flags it on the Health page; the
  compose example marks the volume required.
- Unprobed movies are now eager-probed correctly (the movie file path is
  resolved up front), so they flow from "Analyzing" to verified like episodes
  instead of getting stuck.

## [1.5.1] - 2026-06-11

### Fixed
- **The Overview showed sample/demo rows when there was no real activity
  yet (#193).** New installs saw fabricated "Recent activity" entries for
  files that don't exist (including an impossible `opensubs 429` failure),
  and CPU-only installs saw placeholder GPU numbers. Both now show honest
  empty states. Thanks to u0126 (r/Softwarr) for the report — the rest of
  their dashboard (66k files discovered, 8k Bazarr-wanted) was real and
  working; the demo fallback was manufacturing the confusion.

## [1.5.0] - 2026-06-11

### Added
- **Multiple media locations / libraries (#134).** Media is now modeled as a
  list of libraries, each with its own filesystem root, subgen prefix, and
  *arr path prefix. Managed in Settings → Libraries (and onboarding):
  Sonarr/Radarr root folders not covered by any library surface as one-click
  "Add as library" suggestions, plus a manual add form with live path
  validation; changes apply without a restart. Internally, extra libraries
  qualify canonical keys with a stable `@<id>/` head while the default
  library keeps today's keys — existing installs upgrade with zero
  migration.
- **Multi-arch images — linux/arm64 (#70).** `ghcr.io/coaxk/subarr` now
  builds for amd64 + arm64; Pi 4/5 installs work out of the box.
- **Fleet crash telemetry (#157 Phase 2).** Supervised-loop failures report
  a sanitized aggregate — exception type + module:line + count ONLY, never
  messages/tracebacks/paths — alongside the daily telemetry ping (opt-out
  with the same toggle). Full local detail stays on the Health page; the
  transparency panel shows a crash-reports row and the exact payload.

### Fixed
- **Degraded coverage builds no longer clobber the warm snapshot (#167).**
  A build whose Bazarr/Sonarr/Radarr fetch failed while the cached snapshot
  had it healthy is held (the last good snapshot keeps serving, with a loud
  log), capped at 3 consecutive holds. Kills the ~10-minute all-"Analyzing"
  wall after stack restarts.
- **Library search drill-down (#187).** Clicking a search-matched folder now
  shows its contents — children of a match were previously re-filtered by
  the search term and rendered nothing.
- **Coverage title/path search (#188).** The search box is now a real input
  that filters the table (it was a static placeholder).
- Dead `SONARR_PATH_PREFIX`/`RADARR_PATH_PREFIX` config removed (#133 —
  defined but never consumed; per-library *arr prefixes supersede them) and
  the stale Settings hint referencing them reworded.

### Transparency
- A worker-side validation bug had been rejecting telemetry pings
  fleet-wide since 2026-06-08 (numeric `docker_tier` vs a string-only
  validator). Fixed server-side — no client action needed; install stats
  were undercounted for ~3 days.

## [1.4.0] - 2026-06-09

### Added
- **Job Aftercare (#156).** A post-transcription quality review: every finished
  job is judged for failures + readability and surfaced on a dedicated Aftercare
  page (plus a header pill and a dashboard panel) with a country flag, language,
  source tag, composite score, and a legend. Requeue from the row. Honest by
  design — it flags problems, it never hands out a confident grade.
- **Default-audio-track mismatch detection + one-click fix (#159, #170).**
  Detects when a file's *default* audio track isn't the original language (the
  setup that makes Whisper double-translate) and surfaces it in Review with an
  in-place track swap (`mkvpropedit`, lossless) or dismiss — single or bulk.
  Dismissals are applied at read time so cleared rows stay cleared.
- **Queue authority over every submission (#169).** Manual scans and requeues
  now route through the pending queue like coverage + backfill — visible,
  throttled to target depth, reorderable (step-wise up/down) — instead of
  bypassing it and flooding subgen. Manual stays near-instant (top priority + an
  immediate feeder kick).

### Changed
- **Verified subtitle segmentation baked into the subgen image (r8 /
  "strongpad").** The regroup config tuned in the segmentation arena is now the
  `subarr-subgen` image default — roughly halves hard-to-read high-CPS cues and
  eliminates sub-half-second micro-cues, validated across multiple languages.
  Overridable.
- **Repo quality hardening.** Enforced `ruff check` + `ruff format` as a CI gate
  and a pre-commit hook (previously configured but never run); added a pytest
  gate on pull requests; hash-pinned all GitHub Actions to commit SHAs
  (dependabot keeps them current); added a blocking `zizmor` workflow-security
  gate. Enabling the lint gate surfaced the two bugs below.

### Fixed
- **Bazarr error paths raised `NameError` instead of `IntegrationError`** —
  `bazarr.py` raised it in 14 paths without importing it.
- **Per-episode Bazarr history lookup threw `TypeError` at runtime** — a
  duplicate `episodes_history`/`movies_history` definition shadowed the general
  one. Both now covered by regression tests.
- Pending-queue feeder over-feed race (#116).
- `settle_minutes` and queue controls now persist (#117).
- Bazarr status/badges blip no longer dumps tracebacks; queue panel order
  (Processing → Queued → Pending) and review verify-skip edge cases.

### Thanks
- **u/MrSlaw** (r/bazarr) for a thorough, accurate review of the repo's lint
  state and CI/supply-chain hardening — directly prompted the hardening pass and
  caught the duplicate-method bug.

## [1.3.0] - 2026-06-08

### Added
- **Queue authority — you control the queue now (#66).** subarr holds its own
  pending queue in front of subgen and a depth-aware feeder drains it at a set
  depth instead of flooding. The Queue page gains a **Pending** panel with
  **promote / demote / reorder + pause/resume + target-depth**. Coverage and
  auto-queue jobs route through it (throttled); ad-hoc manual submits and
  requeue stay immediate. subgen's queue is treated as shared — subarr measures
  total depth and never touches jobs other tools queued.
- **Throttled library backfill (#116).** A "Backfill gaps" action loads your
  whole verified-gap backlog at low priority; the feeder drains it gently in the
  background — steady catch-up, no GPU stampede.
- **Settle-window (#117).** Optionally hold a freshly-imported gap out of
  auto-queue for N minutes so Bazarr/providers get first crack at a real sub
  before subarr transcribes. Opt-in per rule; manual transcribe always bypasses.
- **Mis-grouped-series detector (#140).** Flags a series whose episodes resolve
  (high-trust Whisper/user detection) to two or more distinct non-English spoken
  languages — the signature of two different shows merged into one Sonarr series.
  Per-series dismiss for genuinely multilingual shows.
- **Observability — Health page (#157).** Every long-running background loop is
  now supervised: a loop that silently stops working shows up red with its
  captured traceback (Health page + header pill) instead of freezing quietly,
  plus a "report a problem" path.

### Changed
- **Update checker uses the GitHub releases Atom feed (#158)** instead of the
  rate-limited REST API — fixes the `403 rate limit exceeded` that stopped update
  notifications for users behind NAT/CGNAT or busy/shared IPs.
- **GPU is optional + uses the portable `runtime: nvidia` form (#162).** subarr
  never transcodes (subgen does) — its only GPU use was nvidia-smi for the
  Monitor sparkline, so it no longer forces a Swarm-style GPU reservation.
  Removes a startup snag for GPU-less hosts.
- **Docker access via a hardened read-only socket-proxy by default.** The example
  compose no longer mounts the raw docker socket — subarr reaches Docker only
  through a metadata-only proxy, removing the host-RCE blast radius if subarr is
  ever exposed/compromised. Trade-off: in-app subgen auto-restart is disabled
  under this default (mount the raw socket if you want it).

### Fixed
- `settle_minutes` and the queue feed controls now persist when saved (the rules
  API was silently dropping fields it didn't declare).
- The Bazarr-badge probe no longer dumps an "unretrieved task" traceback on a
  transient Bazarr blip.

### Security
- **Onboarding state no longer returns *arr/Plex API keys in cleartext.**
  `GET /api/onboarding/state` now masks `*_api_key` / `*_token` / `*_password`
  values, with a merge-guard so a resuming wizard can't overwrite a stored key
  with the mask.
- **Supervised-task tracebacks redact credential query-string params** (Tautulli
  `?apikey=`, Plex `?X-Plex-Token=`) before storage, so the Health endpoint can't
  leak a key.
- `/api/health` (the one pre-auth endpoint) trimmed to `{status, version}` — no
  more `media_root` / `subgen_url` leak.
- Auth-disabled now logs a loud warning; deploy docs document `SUBARR_USER` /
  `SUBARR_PASS` and the socket exposure risk.

## [1.2.1] - 2026-06-07

### Fixed
- **Coverage refresh could silently freeze on foreign-language libraries.** A
  regression in the v1.2.0 Bazarr-blind defense pass referenced an out-of-scope
  variable, so any coverage walk that produced a Bazarr-blind synthetic row
  (libraries with foreign-language content) crashed in the background — the
  coverage cache stopped updating and resolved gaps never cleared. Fixed; the
  walk completes and resolved files drop out as expected. English-only libraries
  were unaffected.

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
