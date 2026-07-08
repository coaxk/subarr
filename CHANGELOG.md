# Changelog

All notable changes to subarr are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/). Versions follow
[Semantic Versioning](https://semver.org/) — major bumps signal
breaking config changes.

## [Unreleased]

## [2.4.0] - 2026-07-08

**Automatic subtitle re-timing, on by default (#359).** subarr now post-processes every completed subtitle, extending cues that scroll past comfortable reading speed into the silent gap before the next line — so dialogue stays on screen long enough to actually read. Validated off-app across an 1801-subtitle corpus built from real subgen output: cues exceeding the critical 25 characters-per-second reading speed dropped from **22.9% to 5.2%**, and unreadable sub-second "flash" cues fell **77%**, with **zero new overlaps** (every extension is clamped to the available gap). It is idempotent and never shortens a cue or creates an overlap. On by default; opt out with `SUBARR_RETIME_ENABLED=0`.

### Added
- **Genuinely multilingual audio, represented honestly (#357).** A film like *As Bestas* (Galician + Spanish + French in one track) is no longer flagged as a mislabelled English file. subarr recognises confident multi-language audio from per-chunk detection and marks it *multilingual* instead of raising a false "suspect" alarm. Non-linguistic tracks (constructed gibberish, silent-film scores) can be tagged `zxx`. Multilingual and `zxx` files skip the single-language override sent to subgen, so it self-detects per chunk.
- **Correct or accept multilingual verdicts in Review (#406).** Auto-classified multilingual files appear in Review with a 🌐 badge and their own filter tab: accept the detected language set as-is, or correct it with a multi-select picker. Settled verdicts never re-appear in the lane.
- **subarr's own logs are visible (#157).** The Logs page gains a `subgen | subarr` source switcher to tail subarr's internal log live, and the Health page shows a "Recent errors" panel of recent warnings and errors with expandable tracebacks. The opt-in library audio-audit now reports run-level crashes on the Health roster like the other background tasks. A new `SUBARR_DEBUG=1` knob raises logging to DEBUG for local troubleshooting. All local-only — nothing new leaves your box.

### Changed
- **Audio languages are now canonical 2-letter ISO-639-1 (#358).** The audio-language model and the Review picker use 2-letter codes end to end (e.g. `en`, `ko`), normalising on read and write so a mixed 2-/3-letter history resolves consistently.

### Fixed
- **Graceful degradation when an integration client is recycled mid-request (#392, #393).** A Bazarr/Sonarr/Radarr/Plex/Ollama call that raced a client rebuild — or hit a client closed mid-flight — is now translated into a clean, retryable error instead of surfacing a raw `RuntimeError`.
- **Manual "fill this gap" on a movie uploads straight to Bazarr (#394).** The manual movie-gap action now uploads the generated subtitle directly to the owning Bazarr instance, matching the episode path, instead of only nudging its scan-disk task.
- Dependency bumps: `nvidia-ml-py`, `actions/setup-python`, `@playwright/test` (#398, #399, #400).

## [2.3.1] - 2026-06-28

### Fixed
- **In-app version display (#390).** 2.3.0 reported itself as **"2.2.1"** in the header, the "update available" banner, and telemetry — the `__version__` constant wasn't bumped alongside `pyproject` (the two were maintained separately and the release procedure only mentioned the latter). Bumped, plus a CI test that now fails if they ever drift again, and a release-procedure note. No functional change.

## [2.3.0] - 2026-06-28

**Run separate Sonarr/Radarr/Bazarr stacks (#161).** The headline use case is a dedicated **anime** Sonarr + Bazarr alongside your main TV/movie stack — each with its own indexers, providers, and profiles. subarr now models every stack as an **instance**, merges coverage across all of them, and routes every read and every writeback (subtitle uploads, blacklists, rescans) to the instance that owns the row, so a second Bazarr can never receive subs meant for the first. Fully additive — single-stack installs are byte-identical and upgrade with zero migration.

### Added
- **Multi-instance management — Settings → Instances (#161).** Add/test/edit/remove extra Sonarr/Radarr/Bazarr instances with live apply (no restart). Bind each library to a specific instance under Settings → Libraries, with a resolved-topology table (bound arr + Bazarr + the live-matched Plex section) that flags unbound or mis-mounted libraries.
- **Per-instance live health (#378).** Reachability dots per instance on Settings → Instances, per-instance sub-dots on the Integrations tiles, and the global "X/Y healthy" header pill now counts non-default instances — so a down second stack is visible everywhere, not just where it's configured.
- **Library labels across the Gaps surfaces (#378).** Queue, Review, Aftercare, and Activity rows carry a library chip (matching Coverage), so in a multi-stack setup you can see which library each row belongs to at a glance. Default-library rows are unchanged.
- **Browse any library's tree (#378).** Library → Browse gains a root picker (shown only when more than one library is bound) to browse a non-default library's tree, reusing the existing cached, rollup-skipping browser so big libraries stay fast.
- **Onboarding "Run separate stacks?" pointer (#378).** A skippable wizard step introduces multi-instance and points first-run users to Settings → Instances; single-stack onboarding is unchanged.
- **Movie subtitles upload straight to Bazarr (#368).** Generated movie subtitles now upload directly to the owning Bazarr instance (the episode path already did), instead of only nudging Bazarr's scan-disk task. Threaded via a new `radarr_movie_id` on the job → provenance ledger; episodes and manual jobs are unchanged.
- **GPU card works on WSL2-Docker out of the box (#363).** When the `nvidia-smi` binary isn't in the container (the standard WSL2 + nvidia-container-toolkit case, where only the NVML library is mounted), the GPU card now falls back to NVML for its vitals — no more "no telemetry" while CUDA runs fine, and no manual `nvidia-smi` bind-mount. nvidia-smi stays primary.

### Fixed
- **Per-instance Bazarr scan-disk cooldown (#371).** The scheduler's stale-disk poke used a single global cooldown, so a recent poke on one Bazarr stack could delay another stack's rescan; it's now tracked per instance and only advances for an instance that actually fired.
- **Per-instance retry isolation (#372).** An unexpected error from one Bazarr instance's task lookup could abort the completion-watcher's retry pass for the remaining instances; each instance is now isolated.

## [2.2.1] - 2026-06-26

**Critical hotfix: the entrypoint no longer chowns the shared media mount (#369).**

### Fixed
- **Entrypoint reconciles only subarr's own inodes — never the media library (#369).** The non-root entrypoint (added in 2.0) ran `chown -R $PUID:$PGID /data` at boot, with a top-level-only guard that made a full-tree recursive chown *more* likely to fire silently. When the media library was mounted at `/data` (common on NAS setups), a routine `compose pull` rewrote ownership of the entire dataset — and any co-located foreign data (e.g. a Nextcloud data dir) — to subarr's uid, taking out unrelated services. It also chowned `/data` while the DB could live at `/config`, so it missed its own target and the app could still crash-loop on a readonly database. The entrypoint now **never recursively chowns a parent tree.** It reconciles ownership of only subarr's own inodes — the state-dir node itself (non-recursive, so new files can be created), the DB (`+ -wal`/`-shm`), `subarr-overrides.json`, `subarr.lock`, and subarr's own subtrees (`vad/`, `backups/`, the HF cache, now under the state dir) — so a media tree co-located inside the state dir survives untouched. It follows `SUBARR_DB_PATH` wherever it points (fixing `/config` setups and the readonly-DB crash), refuses to act if the path resolves to `/` or a relative dir (no more `chown -R /` from a stray `SUBARR_DB_PATH=/data`), and surfaces chown errors instead of swallowing them. The media library is foreign, read-mostly data and is never re-owned — in multi-library setups every media mount must be owned/writable by `PUID` (subarr writes sidecars there). Keeping `SUBARR_DB_PATH` on a dedicated volume (`/data` or `/config`) remains recommended. Thanks to @tikibozo for the detailed root-cause report.

## [2.2.0] - 2026-06-22

**Bazarr-parity force/ignore controls, per-title ignore, and a deep pass on reliability.** This release closes the long-running #317 trio — blacklist a bad sub, fill forced-only gaps, and find the controls that were scattered across the app — adds per-title ignore, and hardens the queue, database, Plex client, and event loop after heavy real-world use.

### Added
- **Blacklist a bad Bazarr sub, with a history panel (#317 Slice A).** When a provider-downloaded subtitle is broken, you can now blacklist it so Bazarr stops re-fetching the same release. A shared **Blacklist** panel opens from Aftercare (on the "this sub is bad" row) and from the Library tree (on any video file), lists that file's Bazarr download history, and blacklists the offending provider sub through Bazarr's existing API. Built and verified against the live Bazarr `/api/episodes/history` schema. Only real provider downloads are blacklistable — manual uploads are left alone.
- **Transcribe a full sub anyway on forced-only files (#317 Slice B).** Files whose only embedded subtitle is forced-only were shown but skipped (the **"subgen will skip (forced-only)"** chip). Each such row now gets a **"Transcribe full sub"** button that tells subgen to ignore the forced-only track for that one file and generate a complete subtitle — no global `IGNORE_FORCED_SUBTITLES` flip. The request rides the normal pending queue, so GPU throttling, provenance, and the Bazarr scan trigger all still apply, and it survives a restart (the flag is persisted). Requires the subarr-subgen image shipping patch v4.15 (`ghcr.io/coaxk/subarr-subgen:2026.05.3-r10` or newer); on older subgen the bucket keeps the old "set the global knob" guidance.
- **A home for the scattered subtitle controls (#317 Slice C).** Force, ignore, and language controls live in the spots where you actually use them — but they were hard to find as a set. A new **"Other subtitle controls"** directory card on the Rules page names each family in plain terms and links to where it lives (forced subtitles to Coverage, ignored titles to Library, language rules to Settings and Review, audio-language review to Review). Plus a few cross-links where the instinct lands, like Settings language rules to Review and the Coverage forced-only bucket to the hub. Nothing was moved or duplicated — it's signposting only.
- **Per-title ignore, inline (#316).** Tell subarr "I don't want subs here" directly on a Library tree row or from the Review surface. Ignoring a folder suppresses the whole series prefix; ignoring a file suppresses just that file. Ignored rows are visibly muted, and descendants of an ignored folder show **"ignored ↑"** instead of a redundant control. Offered on real titles only, not category roots or sidecars.
- **Bulk Swap / Dismiss for track-mismatch shows (#310).** After the Review TV/Movies split, track-mismatch rows still had to be handled one at a time. Select-all now covers them, and the bulk bar offers **Swap all** and **Dismiss all**. Each swap is re-validated server-side against a live probe, per-file failures (non-MKV, unflagged, already-correct) are reported rather than fatal, and the coverage cache refreshes once at the end. (This also fixed a latent bug where single-file track swaps were always 502-ing on a wrong `Path` import.)
- **Per-sweep reading-speed bar in the Tuning Lab (#314).** The CPS (characters-per-second) threshold the readability judge scores against is now tunable per sweep, with a **"Reading-speed bar (CPS)"** field (Comfortable / Critical, prefilled 20/25). Every recipe in a run is judged against the same bar so rankings stay meaningful. Aftercare's own CPS flagging stays at the 20/25 default — tuning lives in the Lab.
- **Aftercare bulk-acknowledge (#313).** A first-run library audit can produce thousands of aftercare items, and they could only be cleared one click at a time. An **"Acknowledge all"** button now clears every pending item (respecting the current source filter) in a single pass. The confirm dialog spells out the scope and reminds you that subtitle files are untouched.
- **Review split into TV Shows and Movies (#309).** The audio-language Review list is now grouped into separate TV Shows and Movies sections instead of one flat list.
- **Database backup on demand, with a deep integrity check (#291).** The Health page gains a **DB maintenance** card: **"Back up database now"** writes a clean, defragmented, timestamped copy (atomic `VACUUM INTO`, keeping the 5 most recent) and **"Run full integrity check"** runs a deep `PRAGMA integrity_check`. If the database-integrity health pill goes unhealthy, a callout surfaces right there urging a backup before you make changes. Advisory only — nothing is write-blocked.

### Fixed
- **The UI no longer freezes under load (#308).** A dogfood report of sluggishness traced to `/api/queue` doing a synchronous directory scan plus a stat per sibling file for every skipped row, on every poll — over a NAS mount that froze the single event loop ~1.5s every few seconds, stalling every concurrent request. That filesystem work (and the dashboard's full probe-table load) now runs off the event loop, and a new event-loop stall monitor stays in as standing observability. Live result: the recurring multi-second stalls went to zero. A related idle-state fix skips the docker-logs read in `/api/queue` when nothing is processing (#307).
- **Database durability hardening (#291, #302).** Closed a migration race, re-issue WAL mode correctly, and warn on NFS-backed databases where SQLite locking is unreliable.
- **Plex client robustness (#290, #300, #295).** Added connection pooling and a circuit breaker so a flaky Plex doesn't hang requests, with breaker open/recover logged, a non-JSON contract for base `_post`/`_put`, and a Radarr `movieId` fan-out fix.
- **Queue and provenance reconciliation (#287, #301, #297).** Orphaned `SUBMITTED` queue rows are reconciled when the feeder boots, and open provenance rows are de-duplicated with a correct first-stamp on completion.
- **Telemetry hardening (#289, #296).** The telemetry payload is now shape-locked and the task-health reporter is supervised so it can't fail silently.
- **Auth hardening (#292, #293).** Added a password-length DoS cap, equalized login timing to resist user-enumeration, and improved setup-race logging.
- **Logs page tells you when it can't reach Docker (#328).** Instead of an empty or confusing view, the Logs page now shows a clear panel when the Docker socket is unavailable.
- **httpx no longer floods the info log (#325).** Routine health-poll `200`s were spamming the info log; they're now quiet.
- **Onboarding is re-enterable (#318, #320).** You can re-enter the onboarding flow to reconfigure instead of being locked out after first setup.
- **Smaller fixes.** Indent Gaps TV shows under their "TV Shows" header (#312); the aftercare repeats chip only flags at the backend threshold (#315); the coverage-cache health threshold now accounts for refresh build time (#303); prune orphan update-checker product rows, including the stale vanilla-subgen row (#306); coverage Radarr lookup by id with a capped Sonarr fan-out (#286); arena delete now cancels the live task and blocks resurrection (#283); path and subgen-client robustness (#285, #288); plus arena concurrency hardening and a documented pre-merge review policy (#284).

### Internal
- SPA bundles are now auto-rebuilt by a pre-commit hook, closing the recurring bundle-drift problem (#332).

## [2.1.0] - 2026-06-19

**Tuning-Lab GPU coordination + dogfood fixes.** The arena now respects your subgen concurrency limit, plus a batch of fixes from real-world use.

### Added
- **The Tuning-Lab arena respects your subgen GPU-concurrency limit (#279).** The
  arena drives subgen's `/asr` endpoint directly — outside subgen's worker pool —
  so it used to ignore `CONCURRENT_TRANSCRIPTIONS` and could fight queue jobs for
  the GPU. subarr now reads subgen's live limit and gates both the queue feeder
  and the arena on one budget (`subgen processing + arena in-flight < N`). A sweep
  launched while the GPU is busy waits, showing **"Waiting for subgen capacity"**
  on the Tuning Lab card, and the Queue page shows a **"Tuning Lab sweep running"**
  indicator so you can see the contention. Gating is dormant on older subgen and
  activates automatically once subgen reports its limit (subarr-subgen r10+).

### Fixed
- **Update nudge no longer fires when you're ahead of the latest release (#275).**
  A locally-built install (or the window just after a release is tagged but
  before it publishes) could show "vX is out — you're on vY (N releases behind)"
  when Y was actually *newer* than X. The checker now compares versions
  directionally — it only nudges when the running version is strictly older.
- **The arena no longer fails on 3-letter language tags (#278).** Arr/ffprobe tag
  audio in 3-letter ISO 639-2 (`ger`/`fre`/`jpn`); Whisper's `/asr` only accepts
  2-letter ISO 639-1 (`de`/`fr`/`ja`). The arena now normalizes the source
  language, so foreign-language sweeps stop erroring with "'ger' is not a valid
  language code". Un-mappable tags fall back to auto-detect.
- **"Whisper anyway" in the Gaps arbiter now actually queues the job (#277).** The
  button silently closed the dialog without queuing; it now submits the
  transcription and confirms.
- **Batch language overrides normalized to ISO 639-1 (#280).** `forceLanguage` and
  `audio_language_override` are normalized to 2-letter at the subgen wire boundary,
  matching the `/asr` path and removing any ambiguity for subgen.

## [2.0.0] - 2026-06-18

**Security hardening + activation.** subarr now requires authentication by default and runs as a non-root user.

### Upgrading
- **You'll see a one-time login screen.** subarr now requires auth by default —
  create an admin account on first launch, then a normal login page + session
  cookie. Behind a reverse proxy that already authenticates? Set
  `SUBARR_AUTH_DISABLED=1`. Locked out? `SUBARR_AUTH_RESET=1`, env
  `SUBARR_USER`/`SUBARR_PASS`, or `docker exec subarr python -m subarr.cli reset-auth`.
  Existing `SUBARR_API_KEY`/`SUBARR_USER` installs are **not** forced into setup.
- **Hardened-compose users:** the container now runs non-root and its entrypoint
  fixes `/data` ownership at boot, so add
  `cap_add: [CHOWN, SETUID, SETGID, FOWNER, DAC_OVERRIDE]` alongside your
  `cap_drop: [ALL]`, and set `PUID`/`PGID` to the uid that owns your `/data` +
  media (now honoured). See the README hardened-deployment section.
- Everything else upgrades transparently — existing installs keep working and
  `/data` is chowned automatically.

### Added
- **Pause/resume the schedule from the dashboard (#252).** The "Next scheduled
  run" card gains a Pause/Resume button next to Run now, so you can halt or
  restart automation without opening the Rules page (manual + Run now still work
  while paused).

### Security
- **Runs as a non-root user now (#237).** The container drops to `PUID`/`PGID`
  (default 1000:1000) — the entrypoint starts as root only to fix `/data`
  ownership (so existing installs keep working after upgrade) and grant docker-
  socket access, then drops privileges before the app starts. `PUID`/`PGID` are
  now real (previously decorative). The LaBSE QE model cache moved to
  `/data/.cache/huggingface` so it persists. See the hardened-deployment compose
  notes for the minimal capability set.

### Added
- **Onboarding-funnel telemetry (#202).** Anonymous pings now carry a coarse
  `onboarding_step` + `onboarding_complete` so we can see where first-run drops
  off (and improve it), and subgen reachability is re-probed right before each
  send so `subgen_kind` reflects current state rather than the boot snapshot.
  Still privacy-by-construction — coarse, non-identifying.
- **Auto-run the first coverage walk on setup completion (#202).** Finishing
  onboarding now kicks the first walk automatically (when an arr is configured
  and no walk has run yet), so you land on a populated dashboard instead of an
  empty one. A new dashboard empty-state ("No coverage data yet — run your first
  walk") is the safety net for anyone who still lands walk-less.

### Fixed
- **Telemetry no longer transmits under tests/CI (#202 follow-up).** The pinger
  POSTs on its first tick and the endpoint default is the real prod URL, so every
  test that booted the app was pinging telemetry.subarr.com — inflating the
  public install count with our own dev/CI runs. The test suite now hard-disables
  the endpoint, and the pinger is a no-op when no endpoint is configured.

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
