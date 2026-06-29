# #358 — Audio-language canonicalization + complete picker

**Issue:** [#358](https://github.com/coaxk/subarr/issues/358) — "Audio-language picker omits languages (e.g. Galician)."
**Date:** 2026-06-29
**Scope:** B (format consistency **+** picker completeness vs the full Whisper set).
**Out of scope:** the separate `p=0.00` chunk-probability sub-issue (its own slice).

## Problem

Subarr handles language codes in **three different formats** across five+ consumers, and they don't reconcile:

| Consumer | Format today |
|---|---|
| Manual picker (`LANG_PICKS` in `coverage.jsx`) | 3-letter ISO-639-2 (`glg`, `fre`, `ger`) |
| Whisper detection / `normalize_lang` | 2-letter ISO-639-1 (`gl`, `fr`, `de`) |
| Coverage chips, "Accept as Whisper-verified (gl)", display | 2-letter |
| `audio_lang_store` | mixed — stores whatever the caller passes, only `.lower()`'d (schema comment wrongly says "3-letter") |
| subgen `audio_language_override` (`resolve_audio_language_override`) | 3-letter ISO-639-2/B (to match subgen's `SKIP_IF_AUDIO_LANGUAGES=eng`) |
| Sonarr propagation → Bazarr sync (`_iso_to_sonarr_name`) | hand-curated code→name map, ~40 langs, **no Galician** |

Concrete failures:

1. **Picker/store mismatch.** Picking "Galician (glg)" stores `glg`; Whisper detected `gl`. They're treated as two different languages, breaking the agree/suspect heuristic and the display.
2. **Picker incompleteness.** `LANG_PICKS` is a curated ~46-language list. Whisper can return ~99. A user cannot always confirm what Whisper detected — a detect-then-confirm dead end.
3. **Silent Bazarr-handoff footgun.** `_propagate_to_sonarr` maps the code → an English name via the curated `_iso_to_sonarr_name`; its fallback returns the **raw code** for anything unmapped (Galician + the entire long tail), which never matches a Sonarr language name → propagation silently no-ops (`{ok: False, detail: "Sonarr has no language named 'gl'"}`). The optional Bazarr "Sync with Sonarr" courtesy-trigger never fires, with no honest signal to the user.

A prior naive attempt (normalize the store to 2-letter only) **broke the subgen-override contract** — the override forwards the stored code verbatim and subgen needs 3-letter. Two contract tests caught it (`test_coverage_queue_forwards_audio_language_override_when_verified`, `test_requeue_carries_audio_language_override`). That informs this design: the conversion must happen **at the subgen boundary**, not by changing what subgen receives.

## Decision: 2-letter ISO-639-1 is the canonical internal format

2-letter is already what the **majority** of subarr uses (Whisper, coverage chips, display, `normalize_lang`, Bazarr's `code2`). Make the store and picker conform; convert to 3-letter only at the **one** boundary that needs it (subgen override). The Sonarr-name boundary already accepts both and is being upgraded anyway.

### Bazarr alignment (verified, no footgun on the direct path)

Everything subarr sends to / reads from Bazarr's API directly is 2-letter `code2` / defaults to `"en"`:
- `episodes_wanted` → reads `missing_subtitles[].code2` (2-letter).
- `blacklist_episode`, `candidate_episode_subtitles` → send `language` defaulting to `"en"` (2-letter), and these concern the **subtitle target**, not audio.

The 2-letter canonical lines up natively. The only Bazarr-adjacent risk is the **indirect** Sonarr-propagation path (touchpoint 7 below), which this design closes.

## Components

### 1. `WHISPER_LANGUAGES` — single source of truth (`langs.py`)

A table of the ~99 languages Whisper can return: `2-letter code → English name` (lifted from Whisper's own `LANGUAGES` dict). Everything language-facing derives from this one table. This replaces the curated `LANG_PICKS` (frontend) and becomes the name source for the Sonarr propagation.

### 2. Helpers (`langs.py`)

- `normalize_lang(x) → 2-letter` — **unchanged** (already collapses name/iso1/iso3 → 2-letter; idempotent; never raises).
- **new** `to_iso3(code) → 3-letter ISO-639-2/B` — `gl→glg`, `ko→kor`, `fr→fre`. Uses the **B (bibliographic)** code set to match subgen's `SKIP_IF_AUDIO_LANGUAGES` and the override docstring. Falls back to the input unchanged if unmapped (defensive — never raises, never blanks).
- **new** `display_name(code) → str` — English name from `WHISPER_LANGUAGES`, for UI/propagation; falls back to the code if unknown.

### 3. Store = 2-letter always (`audio_lang_store.py`)

- `upsert` normalizes `lang_code` via `normalize_lang` on **write**.
- `get` / `get_all_as_lookup` normalize on **read** too, so legacy mixed-form rows (`glg`/`fre`) come back as 2-letter **without a SQL data-migration**. (Idempotent double-normalize is harmless.)
- Fix the stale `-- 3-letter ISO 639-2/B` schema comment to read 2-letter ISO-639-1.

### 4. subgen-override boundary converts to 3-letter (`resolve_audio_language_override`)

Reads the now-2-letter stored value and returns `to_iso3(lang)`. Subgen always receives the 3-letter code its skip-list expects.
- Round-trips the regression tests: seed `fre` → store `fr` → override `to_iso3('fr')` = `fre`. ✅
- **Fixes a latent bug**: a Whisper-detected `ko` was previously forwarded as 2-letter `ko` (may not match subgen's 3-letter skip list); now `kor`.
- The `en`/`eng` short-circuit stays (no override for English).

### 5. Endpoint normalization (`routers/audio_lang.py::upsert_verification`)

Normalize `req.lang_code` once (`normalize_lang`) and use it for the store call, the Sonarr propagation, and the response body — so all three agree on 2-letter. (The store also normalizes defensively; belt and suspenders.)

### 6. `/api/languages` endpoint + complete picker

- New `GET /api/languages` → `[{code, name}]` for the full `WHISPER_LANGUAGES` set, sorted by name.
- Frontend: a fetch-once-and-cache `languages.mjs`; the three dropdowns (coverage row, coverage bulk, review bulk) build from it, retiring the hardcoded `LANG_PICKS`. The picker now sends 2-letter natively (mismatch gone at the source) and offers every language Whisper can detect (no dead ends).

### 7. Sonarr-propagation coverage + honest degradation (`_iso_to_sonarr_name` → name resolution)

Replace the curated `_iso_to_sonarr_name` map with `display_name(code)` from `WHISPER_LANGUAGES` (all 99 names), plus a small **alias-override** map for the handful where Whisper's English name ≠ Sonarr's (e.g. Whisper "Greek" vs Sonarr naming). Then:
- Match the resolved name against Sonarr's **live** `/api/v3/language` list (already fetched in that function — no hardcoded assumption about Sonarr's coverage).
- If Sonarr supports it (Galician, Catalan, etc.) → propagate + trigger Bazarr sync. ✅
- If Sonarr genuinely can't represent it → return a clear, **honest** non-fatal result: *"Sonarr doesn't support X — your local verification and the subgen override still apply."* The local verification + subgen override (the parts that actually fix the missing subtitle) **always** persist; only the optional courtesy-sync degrades, and it now says so.

## Data flow (Galician, end to end)

```
Whisper detects audio          → "gl"  (2-letter)
Store (whisper source)         → "gl"
User opens picker (/api/languages) → "Galician" / value "gl"  (native 2-letter)
User confirms                  → POST /verifications {lang_code:"gl"}
  store.upsert                 → "gl"   (normalize, idempotent)
  resolve_audio_language_override → to_iso3("gl") = "glg" → subgen  ✅ (matches SKIP list)
  _propagate_to_sonarr         → display_name("gl") = "Galician"
                                  → matched in Sonarr /language? yes → PUT + Bazarr sync ✅
                                                              no  → honest "Sonarr can't hold Galician"
Agree/suspect heuristic        → store "gl" == whisper "gl"  ✅
```

## Migration / legacy

No SQL data-migration. Normalize-on-read in the store handles pre-existing mixed-form rows transparently. (Optional future cleanup: a one-time normalize of existing rows — not required for correctness.)

## Design-validation step (during implementation)

Subarr owns the subgen fork (`coaxk/subarr-subgen`). Before finalizing the `to_iso3` direction, confirm in the subgen source how `audio_language_override` is matched against `SKIP_IF_AUDIO_LANGUAGES` (2- vs 3-letter, B vs T codes) — to be certain, not assume. The docstring + the 3-letter skip list strongly indicate 3-letter/B; verify.

## Testing strategy

**Unit (`langs.py`):**
- `to_iso3` round-trips (`gl↔glg`, `fr↔fre`, `ko↔kor`) and falls back unchanged for unknown.
- `WHISPER_LANGUAGES` contains `gl`/Galician and is the expected size (~99).
- `display_name` + alias overrides resolve correctly.

**Store:** `upsert` normalizes on write (`glg`/`Galician`→`gl`); read paths normalize legacy rows; `get_all_as_lookup` makes picker-`gl` and Whisper-`gl` agree.

**Contract:**
- `resolve_audio_language_override` yields **3-letter** end-to-end; the two existing regression tests stay green (seed `fre` → override `fre`).
- Picker→store→override flow: `gl` → store `gl` → override `glg`.
- `GET /api/languages` returns the full set with Galician present.
- Propagation: a language Sonarr supports → attempted+matched; a language it lacks → honest non-fatal degradation (local verification still persisted).

**Frontend (vitest):** the dropdowns build from the fetched `/api/languages` set; a detected `gl` is selectable; `languages.mjs` fetches once and caches.

## Acceptance criteria

1. Picking any Whisper-detectable language stores a 2-letter code that agrees with Whisper detection.
2. The picker offers the full Whisper language set (no dead ends).
3. subgen still receives 3-letter overrides (regression tests green); Whisper-detected non-English overrides are now 3-letter too.
4. Sonarr propagation succeeds for any language Sonarr supports (incl. Galician) and degrades honestly otherwise.
5. No regression on the existing audio-lang suite; ruff + vitest clean.

## Risk tier

**Tier-2** — touches writeback-to-arr (Sonarr propagation), a cross-service contract (subgen override), and a data-model format change. Multi-lens + failure-mode review before merge per the repo review program.
