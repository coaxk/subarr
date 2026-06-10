# Handoff: #134 Phase 1 — multi-library path layer (library-qualified canonicals)

Written 2026-06-10 for a cold-start session. Read this + issue #134 (the
design lives in its comments) before touching code. Phase 0 is merged; this
doc is the spec-entry-point for Phase 1, the real refactor.

## Mission

Replace the single-`media_root` assumption with a **list of libraries**, each
carrying a filesystem root, a subgen prefix, and an *arr path prefix — so
setups with 3+ disjoint media locations, or multiple root folders inside one
*arr, work without union-mount gymnastics. This is the committed "next major
epic" (user demand on the issue: "people see it doesn't have this and just
move on"); the owner publicly promised it.

## State as of this handoff

- **v1.4.0 released**; main green; 793 tests passing (`PYTHONPATH=src pytest -q --ignore=tests/e2e`).
- **Phase 0 merged** (PR #183):
  - `SonarrClient.root_folders()` / `RadarrClient.root_folders()` → `GET /api/v3/rootfolder`.
  - `GET /api/onboarding/root-folders` — best-effort per service (the ground
    truth for auto-deriving `libraries[]`).
  - `strip_arr_prefix(arr_path, prefix=None)` consolidated in `paths.py` —
    **the optional `prefix` param is the seam Phase 1 threads per-library
    prefixes through.** All 4 former consumers (coverage_engine, scheduler,
    coverage_actions, arr_mediainfo) now use it.
- **PR #181 (arm64) deliberately open** — unrelated; merge whenever.

## The locked design — approach A (from the #134 recon comment)

**Library-qualified canonical form: `@<lib>/relative/path`.**

- `@` is a reserved prefix that can't collide with a real directory name, so
  library identity travels *inside* the existing opaque canonical key. Every
  store, cache, JSON snapshot, and API payload that passes canonicals through
  untouched **stays untouched** — only the path-translation functions,
  config, and onboarding change.
- **The default library uses the EMPTY prefix**: existing keys like
  `"TV/Show/ep.mkv"` remain valid canonicals for library 0 → **near-zero DB
  backfill**. Only additional libraries get `@lib2/...` keys.
- Rejected alternative (B): threading a separate `library_id` through all 16
  call chains + adding a column to all 6 canonical-keyed tables. More
  invasive, real migration; don't revisit unless A hits a wall.

### Config model

`libraries: list[Library]` where `Library = {name, fs_root, subgen_prefix,
arr_prefix}`. Library 0 ≡ today's `media_root` / `subgen_media_prefix` /
`arr_path_prefix` (back-compat: when only the legacy env vars are set, build
a single-library list from them — zero config change for existing installs).
Auto-derive additional libraries from `root_folders()` during onboarding
(Phase 0 endpoint already returns them).

**Cleanup ride-along:** `sonarr_path_prefix` / `radarr_path_prefix` (#133)
are **dead config** — defined in `config.py`, consumed nowhere (verified
2026-06-10). `libraries[]` subsumes them; remove rather than wire.

### Functions that must become library-aware (all in/around `paths.py`)

- `canonical_to_fs` — parse optional `@lib/` head → resolve under that
  library's `fs_root` (keep the traversal guard per-root).
- `fs_to_canonical` — match the owning library by root, emit `@lib/` head
  (empty for library 0).
- `canonical_to_subgen_batch` / `subgen_to_canonical` — per-library
  `subgen_prefix`.
- `strip_arr_prefix` — callers pass the owning library's `arr_prefix` via the
  existing param; deciding the owning library = longest-matching `arr_prefix`.
- Walkers (`probe_walker`, coverage build, audio audit) — iterate all
  library roots; `probe_roots` currently carries no library identity.

## Blast radius (verified numbers)

- ~27 files read `settings.media_root`; 16 direct callers of the path fns.
- **6 tables key on `canonical_path` as PRIMARY KEY**: `media_probe`,
  `lang_enrichment`, `audio_lang_verifications`, `audio_lang_audit`,
  `subs_generated`, `series_lang_intent`. Approach A means their existing
  rows stay valid (default library = empty prefix); no backfill expected —
  but **verify on a copy of the live dev DB** like the init_schema removal.
- Frontend: canonicals are opaque strings end-to-end; main risk is anything
  that *renders* a canonical assuming it starts with a real folder name
  (Library tree grouping, Coverage tree-by-show). Audit those.

## Suggested slices (each its own reviewed PR; from the issue + refined)

1. **`Library` model + config parsing + back-compat single-library
   derivation** — pure config; no behavior change when one library.
2. **`paths.py` library-aware** (all fns above) + exhaustive unit tests
   incl. `@lib/` parsing, traversal guards per root, longest-prefix arr
   matching. The empty-prefix invariant gets pinned here.
3. **Walkers + coverage partition** — multi-root iteration; probe_roots gains
   library identity.
4. **Producers/consumers sweep** — the 16 call sites; most should Just Work
   via the opaque key, verify each.
5. **Onboarding + Settings UI** — surface `root_folders()`, map each to a
   library (fs_root + prefixes), validate reachability (reuse
   `/onboarding/probe-paths` per library).
6. **Live-DB verification + docs** — fresh DB + copy of live dev DB; README
   + deploy templates (multi-mount examples).

## Invariants & gotchas (hard-won, do not relearn)

- **subgen paths ≠ subarr canonicals** (#58 lesson): subgen queue keys are
  subgen-space (`/media/...`) — pass them verbatim to subgen, never
  canonicalize for queue cancel/dedup. The feeder's in-flight set and
  completion watcher match on subgen-space paths.
- **`Settings` is a frozen dataclass** — live-reload swaps use
  `object.__setattr__` (onboarding pattern); tests configure via env vars
  through the `subarr_env` fixture, NOT monkeypatching settings attrs.
- **Env-set fields are authoritative** — never overwritten by wizard progress
  (#33 rule; `config.FIELD_ENV_VARS` / `env_is_set()`).
- Migrations are the single source of truth (no init_schema); `migrate.py`
  tolerates duplicate-column. Next free migration number: check
  `src/subarr/migrations/` (017 was aftercare).
- Tests: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/ -q --ignore=tests/e2e`.
  Windows asyncio-cleanup ResourceWarnings are benign.
- **CI gates on every PR**: ruff check + format (pinned 0.15.15), pytest,
  bandit (`-ll -ii --skip B101`; hoist any f-string SQL to module constants —
  B608), zizmor (blocking), trivy (a fresh base-image CVE reds EVERY open
  PR at once — fix once via `apt-get upgrade` in the Dockerfile, then
  update-branch the rest). Run ruff format BEFORE pushing (the gate caught
  us once).
- Frontend bundles: `npm run build:frontend` after JSX edits; commit only the
  bundle(s) you changed; never hand-merge bundle conflicts — rebuild.
- Deploy loop: subarr-next (:9923) bind-mounts the MAIN worktree's
  `src/subarr` → `docker restart subarr-next` for Python; frontend needs the
  bundle rebuild first. **Test on 9923, never 9922.**
- Merging: branch protection requires review → merge with
  `gh pr merge --squash --admin` once CI is green (per standing practice).

## Acceptance (Phase 1 done when)

- A config with 2+ libraries (disjoint roots, different subgen/arr prefixes)
  walks, probes, covers, queues, transcribes, and completes end-to-end on
  :9923.
- A single-library legacy install (env vars only) behaves byte-identically:
  same canonicals, no DB changes, no UI difference.
- Existing live dev DB loads + coverage matches pre-refactor counts.
- `sonarr_path_prefix`/`radarr_path_prefix` removed (or consciously kept with
  a wired consumer — decide, don't leave dead).

## Process

- Board #6 `PVT_kwHOADDHj84BZfo7`, Status field `PVTSSF_lAHOADDHj84BZfo7zhUd5bo`
  (Todo `f75ad846` / In Progress `47fc9ee4` / Done `98236657`). Move #134 →
  In Progress at start.
- Superpowers flow: this doc + issue #134 ≈ the brainstorm/spec; go to
  writing-plans → subagent-driven or inline execution. Slices 1–2 are the
  foundation — get them reviewed before fanning out 3–5.
