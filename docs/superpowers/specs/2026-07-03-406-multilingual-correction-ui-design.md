# #406 — multilingual audio correction UI

**Issue:** [#406](https://github.com/coaxk/subarr/issues/406) — the deferred #357 correction UI.
**Date:** 2026-07-03
**Scope:** make auto-classified multilingual verdicts **visible and correctable** in the review page, and let a user **create** a multilingual verdict via the picker. Closes the #357 tail: today a confident-multilingual verdict is display-only (badged `🌐 gl·es·fr`, suspect suppressed), with no in-app way to see or correct it — the only fix is the `DELETE /verifications` API.

## Context

#357 (PR #405) shipped the full backend + coverage badge. Already in place:
- Router `POST /api/audio-lang/verifications` accepts `lang_class` + `lang_codes`; the store persists them.
- Auto-record (audit walker Tier-2) writes `source='auto-high-conf-multi'`, `lang_class='multi'`, and **never clobbers a `user` verdict**.
- Tested pure helpers in `review.jsx`: `buildVerifyBody(path, langs)` (1 lang → single, 2+ → multi + `lang_codes`) and `isAutoMultilingualRow(r)`.

The gap is purely UI + one read-endpoint branch. **Why auto rows are invisible today:** in `pending_review` ([routers/audio_lang.py:711](../../../src/subarr/routers/audio_lang.py)) an `auto-high-conf-multi` row has a populated `lang_code` → hits the "already verified → skip", and its suspect flag is suppressed → no flag → dropped.

## Components

### 1. Backend — surface auto rows in `pending-review`

Add a branch to `pending_review`, placed **before** the already-verified skip, keyed on the verification **source** (not the snapshot's display `audio_source`, which is `'multilingual'` for both auto and user-confirmed rows):

- Load `sources = audio_lang_store.get_all_sources_as_lookup()` and `multi = audio_lang_store.get_all_multi_as_lookup()` (both already exist).
- For a row whose `file_canonical_path` (or `canonical_path`) has `source == 'auto-high-conf-multi'`: emit it with `flag = 'multilingual'` and `extra = {'lang_codes': multi.get(path)}`.
- This deliberately **excludes** user-confirmed multilingual (`source == 'user'`, `lang_class='multi'`) — those are settled and must never re-enter the lane.
- Track-mismatch keeps its existing FIRST precedence; the multilingual branch sits alongside suspect/unknown but is emitted for rows the verified-skip would otherwise drop.

The `pending` payload entry gains `lang_codes` in `extra` for the multilingual flag so the frontend can render + pre-fill the set.

### 2. Frontend — multi-select toggle in the bulk toolbar

`review.jsx` bulk toolbar (~:940) gains a "Multilingual" checkbox mirroring the existing "Remember for future" pattern:
- **off** (default): today's single `<select>` — unchanged behaviour.
- **on**: the control becomes a checkable language list (multi-pick), backed by a `bulkLangs: string[]` state.
- Apply routes through the shipped `buildVerifyBody(path, on ? bulkLangs : [bulkLang])` → `lang_class='multi'` + `lang_codes` when 2+ selected, else single. `zxx` is one of the selectable options (already in `WHISPER_LANGUAGES`).
- The "Remember for future" (series-intent) checkbox is **disabled/hidden** while Multilingual is on — a series-level multilingual declaration is out of scope (#357 non-goal); a multilingual verdict is per-file only.

### 3. Frontend — the lane + lifecycle

- Auto-multilingual rows (`flag === 'multilingual'`) render in the existing pending list with a distinct `🌐` chip, **sorted last** (low priority — they are already applied, not blocking).
- **Correction lifecycle (free):** correcting a row (to single, or to a different set) POSTs `source='user'` → on the next `pending-review` fetch its source is no longer `auto-high-conf-multi` → it drops from the lane.
- **Accept action:** an "Accept (keep as detected)" button for selected multilingual rows re-submits **each row's own `lang_codes`** (from the payload `extra`) as `source='user'`, `lang_class='multi'` → confirms-and-clears the correct ones. This is per-row (each carries its own set), distinct from the uniform bulk-assign. It lets the lane empty as the user reviews rather than growing unbounded.

## Data flow

```
audit walker auto-records  -> store {source: auto-high-conf-multi, lang_class: multi, lang_codes}
  -> pending-review branch (source == auto-high-conf-multi)  -> {flag: multilingual, extra.lang_codes}
     -> review list: 🌐 row, sorted last
        -> Correct (toolbar multi-select) -> POST source=user  -> drops from lane
        -> Accept (keep as detected)      -> POST source=user, own lang_codes -> drops from lane
```

## Error handling

- A multilingual payload row missing `lang_codes` (shouldn't happen) → the row still renders; Accept skips it (nothing to confirm), correction via the toolbar still works.
- Accept/correct POST failure → per-file error surfaced in the existing bulk-progress UI (same path as the current single-assign), never a silent no-op.
- `pending-review` source lookups are best-effort reads already guarded by the endpoint's structure; a missing store returns the existing behaviour (no multilingual rows).

## Testing

- **Backend** (`pending-review`): an `auto-high-conf-multi` row is surfaced with `flag='multilingual'` + `extra.lang_codes`; a `user`/`lang_class='multi'` row is NOT surfaced (already settled); a suspect/unknown row is unaffected.
- **Frontend** (vitest, pure helpers): the toggle drives `buildVerifyBody` (single vs multi) — already covered; add a sort helper test asserting `flag==='multilingual'` rows order last; an Accept-payload builder (per-row set → `source='user'` body) tested like `buildVerifyBody`.
- **Regression:** existing single-assign flow, suspect/unknown surfacing, and track-mismatch precedence unchanged.

## Acceptance

1. After an audit auto-classifies a file multilingual, it appears in the review list with a `🌐 multilingual` flag, sorted last.
2. A user can correct it — to a single language, or a different set — and it drops from the lane.
3. A user can "Accept (keep as detected)" a correct one → it becomes `source='user'` and drops from the lane.
4. A user can create a multilingual verdict from any assignable row via the Multilingual toggle + multi-select.
5. User-confirmed multilingual rows never re-appear in the lane. Full suite + vitest green; ruff clean.

## Out of scope (unchanged from #357)

- Per-segment language tracking; series-level multilingual intent.
- Empirical `T` threshold tuning (tracked in #407).

## Risk tier

**Tier-1** — additive read-endpoint branch + UI, no data-model change, no migration. The load-bearing detail is the **source-keyed filter** (auto vs user-confirmed) so settled verdicts don't re-enter the lane; and the `pending-review` branch ordering relative to the already-verified skip.
