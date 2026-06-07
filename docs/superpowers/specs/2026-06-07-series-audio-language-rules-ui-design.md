# Series / Title Audio-Language Rules ("intent") — UI Wiring

**Date:** 2026-06-07
**Status:** Approved (design) — ready for implementation plan
**Issue:** series-intent (#226) shipped backend-only; this wires the frontend.

## Background

The series-intent feature (#226, commit `0f6c45e`) lets a user declare "this whole
show is language X", and every current **and future** episode under that prefix
inherits the audio language as a virtual verification. The store, longest-prefix
matching, per-file override, and coverage-build integration (`_build_verification_lookup`,
the #69 work) are all built and tested.

It was never reachable from the UI, and its three CRUD routes were additionally
double-prefixed (`/api/audio-lang/audio-lang/series-intent`) because the decorators
repeated the router prefix. The route bug is fixed in this session (decorators →
bare `/series-intent`); this spec covers wiring the frontend so the feature is
actually usable.

## Goals

- A **single gesture** on the Review page to declare a whole show/movie's audio
  language as a durable, **coverage-only** rule.
- Existing episodes/files flip green via inheritance; future downloads inherit
  automatically.
- A **Settings management list** to view and revoke rules.

## Non-Goals (YAGNI)

- **No `bulk-for-series` usage** — intent inheritance already covers existing files,
  so the one-shot batch endpoint is redundant for this feature.
- **No Sonarr propagation for intent** — intent rows are virtual/coverage-only.
  (The existing per-file bulk-apply still propagates for the concrete files it
  stamps; that behaviour is unchanged.)
- **No proactive "declare a show I'm not currently reviewing" form** — declaring
  happens through the Review flow; the Settings surface is view/revoke only.
- **No per-rule editing** — revoke + re-declare instead.

## Decisions (from brainstorming)

1. **Scope:** unified gesture covering existing + future files.
2. **Write-back depth:** coverage-only (no Sonarr) for the intent rule.
3. **Placement:** a checkbox in the existing Review bulk-action bar (option A).
4. **Movies:** intent applies to a movie's **folder prefix** (e.g.
   `Movies/Parasite (2019)/`) so the rule survives a re-download/quality upgrade
   that changes the filename. Movies appear in the same management list as shows.
5. **Label:** the checkbox reads "Remember for future downloads" (generalized so it
   reads correctly for both new episodes and movie re-grabs).

## Architecture

### Backend (small — core logic already exists)

`src/subarr/routers/audio_lang.py`:

- **Route fix (done this session):** the `series-intent` PUT/GET/DELETE decorators
  use bare paths so they resolve at the single `/api/audio-lang/series-intent`.
- **`PUT /series-intent`:** after `store.set_series_intent(...)`, kick a coverage
  refresh via `coverage_cache.request_refresh(bundle, probe_store, store)` — the
  same coalescing entry point the per-file `upsert_verification` already uses — so
  existing episodes flip green within seconds instead of waiting for the next
  scheduled walk.
- **`DELETE /series-intent`:** kick the same refresh after a successful delete so a
  revoked rule's episodes revert promptly. A `404` (no such rule) is surfaced as
  already-removed.
- **`GET /series-intent`:** return the stored fields (`series_prefix`, `lang_code`,
  `declared_at`, `note`) plus a **best-effort `covered_count`** computed from the
  coverage snapshot (count of items whose canonical path starts with the prefix).
  If no snapshot is cached, omit/zero the count — never block the list on it.
  `title` and `media_type` are **derived in the frontend** from the prefix (last
  non-empty path segment for the title; a `Movies/`-rooted prefix → movie, else
  show) to avoid extra backend coupling.

### Frontend — declare (Review page)

`src/subarr/static/v1/home-hifi/review.jsx`:

- Add a **"Remember for future downloads"** checkbox to the existing bulk-action
  bar (`selectedCount > 0` block, ~line 550).
- On **Apply** (`applyBulk`):
  - The existing per-file loop runs unchanged (concrete `user` verification rows +
    Sonarr propagation + Bazarr sync for the selected existing files).
  - **If the checkbox is checked**, additionally `PUT /api/audio-lang/series-intent`
    **once per distinct title-prefix** present in the selection, with the chosen
    `lang_code`.
- **Prefix derivation:** group the selected files by their show/movie root. The
  pending-review items carry both `canonical_path` (title/series-level) and
  `file_canonical_path` (the file); the per-group root is the basis for the
  `series_prefix` (normalized to a trailing slash, which the store enforces
  anyway). Exact derivation is pinned during planning.
- **Best-effort:** an intent PUT failure does **not** fail the bulk (the primary
  action); it is counted/surfaced in the existing error indicator.

### Frontend — manage (Settings)

`src/subarr/static/v1/home-hifi/settings.jsx`:

- Add a **"Language rules"** sub-section under the Audio-language rail item.
- Fetch `GET /api/audio-lang/series-intent` and render a **unified alphabetical
  list**:
  - Per row: type icon (📺 show / 🎬 movie), derived title, prefix +
    declared-ago + `covered_count`, language as a **flag chip via the `LangTag`
    atom** (`atoms.jsx`), and a **Revoke** button (`DELETE`).
  - **Filter pills:** All / Shows / Movies.
  - **Internal scroll:** the list scrolls inside the panel.
  - **A–Z ladder** pinned on the right: active first-letters in the violet accent,
    click to jump to that section.
  - **Empty state** when no rules exist.

## Data Flow

- **Declare:** Review checkbox → `applyBulk` fires per-file POSTs (existing) + one
  `PUT /series-intent` per distinct prefix → backend writes the rule + kicks a
  coverage refresh → next coverage build applies inheritance → episodes/files turn
  green → the rule appears in Settings → Language rules.
- **Revoke:** Settings → Revoke → `DELETE /series-intent` → backend kicks a refresh
  → inherited files revert to their underlying classification on the next walk.

## Error Handling

- Intent `PUT`/`DELETE` failures are non-blocking and surfaced inline; bulk
  per-file results stand on their own.
- `DELETE` returning `404` is treated as already-removed (idempotent) and still
  refreshes the list.
- `LangTag` already degrades gracefully for unknown/multi-country codes (bare code,
  no misleading flag); `und` shows no flag.
- `GET` with no coverage snapshot still returns the rules (count omitted/zero).

## Testing

- **Backend**
  - Route resolves at the single `/api/audio-lang/series-intent` and not the
    double-prefixed path. ✅ done — `tests/test_audio_lang_series_intent_routes.py`.
  - `PUT` kicks a coverage refresh.
  - `DELETE` kicks a coverage refresh; `404` path is idempotent.
  - `GET` returns the expected shape including best-effort `covered_count`.
  - Inheritance behaviour already covered by `tests/test_coverage_series_intent.py`.
- **Frontend**
  - A small pure-logic test for the prefix-derivation helper and the
    alphabetical/ladder grouping helper (proportionate to the repo's frontend-test
    posture).
  - Manual / Playwright spot-check of declare → green → revoke is optional.

## Deploy Notes

- subarr-next bind-mounts the active worktree; `wsl -e bash -lc "docker restart
  subarr-next"` reloads Python. Frontend changes require `npm run build:frontend`
  first (bundles are served, not the raw `.jsx`).
- Local pytest needs `PYTHONPATH=C:\Projects\subarr\src` (the editable install
  points at a stale path).

## Files Touched

- `src/subarr/routers/audio_lang.py` — refresh kicks on PUT/DELETE; `GET` enrichment.
- `src/subarr/static/v1/home-hifi/review.jsx` — bulk-bar checkbox + per-prefix PUT.
- `src/subarr/static/v1/home-hifi/settings.jsx` — Language-rules management section.
- `src/subarr/static/v1/home-hifi/atoms.jsx` — reuse `LangTag` (no change expected).
- Tests as above.
