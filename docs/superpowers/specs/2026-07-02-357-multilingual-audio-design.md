# #357 — represent genuinely multilingual + non-linguistic (`zxx`) audio files

**Issue:** [#357](https://github.com/coaxk/subarr/issues/357) — audio-language review: represent files that legitimately have no single audio language.
**Date:** 2026-07-02
**Scope:** stop forcing one audio-language pick per file. Recognise two classes that break the single-language assumption from opposite ends — **multilingual** (e.g. *The Beasts* / *As bestas*, gl+es+fr, where high-confidence chunk disagreement **is** the answer) and **non-linguistic** (e.g. *Junk Head*, constructed gibberish → `zxx`) — represent them correctly, and **stop the false `suspect` alarm** on confident-multilingual files.

## Context & the pivotal finding

subarr's audio-language model assumes one true language per file. Detection runs subgen's `/detect_language_robust` (multi-chunk Whisper vote across the middle of the file); `parse_robust_detect` ([arena.py:66](../../../src/subarr/arena.py)) aggregates the per-chunk languages into a verdict shape (`unanimous` / `n_agreeing` / `languages_heard`), and `resolve_source_language` ([arena_service.py:49](../../../src/subarr/arena_service.py)) turns that into one language + confidence. A no-majority split (1 gl / 1 es / 1 fr) currently lands in the **"confused"** bucket and the file is flagged `⚠ suspect / 1/3 agree` — but for *The Beasts* the disagreement is not confusion, it is the multilingual answer.

**Pivotal finding (bounds the scope):** sub-needed is **not** audio-language-derived. Coverage gaps come from Bazarr's `wanted` list; the audio-language value only drives three things — (1) the **suspect/mislabel flag**, (2) the **`audio_language_override`** forwarded to subgen's `/asr` (its `SKIP_IF_AUDIO_LANGUAGES` gate), and (3) **UI display**. So representing a file as multilingual does **not** require any coverage/sub-needed re-architecture. The concrete bug is that subarr cries wolf on a confident answer and would forward a wrong single-language override.

**Second finding (enables the fix):** subarr currently **discards the per-chunk probabilities**. `parse_robust_detect` reads only `chunks[].language` and counts votes; it never inspects each chunk's confidence — even though #396 just made those probabilities real in subgen. That discarded signal is exactly what separates "3 confident chunks in 3 languages" (multilingual) from "3 low-confidence guesses on a musical score" (genuinely confused).

## Decisions (locked in brainstorming)

1. **Ambition:** *represent + stop crying wolf.* No coverage/sub-needed re-architecture; no per-segment language tracking. (Both are explicit non-goals.)
2. **Detection rule:** use per-chunk confidence (the discarded #396 probabilities).
3. **Confirm model:** *auto-record when confident, but flag for glance* — a confident multilingual detection is stored automatically (suspect suppressed) and surfaces in a low-priority "auto-classified multilingual" review lane for bulk eyeball/correction.
4. **Threshold:** ship `T = 0.5` as an env-overridable default. Empirical tuning is deferred (see Deferred), because there is no chunk-probability corpus yet and multilingual is a rare class.

## Components

### 1. Detection — capture per-chunk confidence + the confident-multilingual rule

Extend `parse_robust_detect` to also capture each chunk's probability (currently dropped). Add a classification step consumed by `resolve_source_language`:

- `high_conf_langs` = the set of languages that have **at least one chunk with `probability ≥ T`** (`T` default `0.5`, from `SUBARR_MULTILANG_CHUNK_MIN_PROB`).
- **`len(high_conf_langs) ≥ 2` → confident multilingual.** The stored set is `high_conf_langs`. (*The Beasts*: {gl, es, fr}.)
- **`len(high_conf_langs) == 1` → single language** (that one) — low-confidence "noise" chunks in other languages are ignored, so this does **not** regress ordinary single-language files that had one stray misdetected chunk.
- **`len(high_conf_langs) == 0` → confused** → fall back to the tag (today's behaviour, unchanged).

This slots in *ahead of* the existing `unanimous`/`bilingual`/`confused` logic: only when it does **not** fire confident-multilingual does control fall through to the current verdict path. Single-language detection is otherwise untouched.

**Feasibility gate (first implementation step):** verify subgen's `/detect_language_robust` response actually includes a per-chunk `probability` (or equivalent) field. If it does not, capturing it is a small subgen-side prerequisite that must land first. The rest of this design assumes the field is present post-#396.

### 2. Data model — store a set without breaking singular consumers

Augment the existing `audio_lang_verifications` table (and, symmetrically, `series_lang_intent` is **out of scope** — series-level multilingual intent is not part of this slice):

- Add `lang_class TEXT NOT NULL DEFAULT 'single'` — `'single'` | `'multi'`.
- Add `lang_codes TEXT` (JSON array) — populated **only** when `lang_class = 'multi'`; holds the ordered set, e.g. `["gl","es","fr"]`.
- The existing singular `lang_code` column is **always** populated: for a multi file it holds the plurality / first-of-set pick, so **every existing single-language consumer keeps working unchanged** (it reads `lang_code`). Multilingual-aware consumers read `lang_codes` when `lang_class = 'multi'`.
- **`zxx` needs no new structure** — it is a single-value verdict: `lang_code = 'zxx'`, `lang_class = 'single'`.

Migration: one new versioned SQL file adding the two nullable/defaulted columns (no PK change, no backfill needed — existing rows default to `'single'`).

*Alternatives considered and rejected:* composite PK `(canonical_path, lang_code)` with N rows/file (rejected — changes the one-row-per-path PK that consumers rely on widely; high blast radius); a separate `audio_lang_multi` table (rejected — two sources of truth for "the audio-language answer").

### 3. Behaviour — auto-record, glance lane, override suppression

- **Auto-record:** a confident-multilingual detection is written to `audio_lang_verifications` with `source = 'auto-high-conf-multi'`, `lang_class = 'multi'`, `lang_codes = high_conf_langs`, mirroring the existing `auto-high-conf` single-language path. The `⚠ suspect` flag is **suppressed** for these files (they are a confident answer, not a mislabel).
- **Glance lane:** auto-classified multilingual files remain visible in a low-priority review lane (a filter/section of the existing pending-review surface) so the set can be eyeballed and corrected in bulk. They do **not** block coverage or nag.
- **Override suppression:** multilingual and `zxx` files **skip** the `audio_language_override` forward to subgen (`resolve_audio_language_override` returns "no override") — there is no single source language to declare, so subgen falls back to its own per-chunk detection.

### 4. `zxx` (non-linguistic) — manual label

- Add `zxx` to the selectable `WHISPER_LANGUAGES` list ([langs.py](../../../src/subarr/langs.py)) — it is already in the ISO map, just absent from the picker set — with a human label like `zxx — No linguistic content`.
- `zxx` is **user-applied only** (not auto-detected; Whisper cannot reliably emit it). Marking a file `zxx` stores `lang_code = 'zxx'`, `lang_class = 'single'`, and suppresses the suspect flag + skips the override like multilingual.

### 5. UI

- **Coverage badge:** a new `multilingual` state replaces `⚠ suspect` for confident-multilingual files, rendered as `🌐 gl·es·fr` (the `lang_codes` set). `zxx` renders as its own badge/label.
- **Review modal picker:** gains **multi-select** so a multilingual verdict can be corrected (add/remove languages); `zxx` is selectable in the same picker. Confirming a multi-select writes `lang_class = 'multi'` + `lang_codes`; confirming a single language writes `lang_class = 'single'` as today.
- The glance-lane filter surfaces `source = 'auto-high-conf-multi'` rows.

## Data flow

```
subgen /detect_language_robust {chunks: [{language, probability}, ...]}
  → parse_robust_detect  (NOW captures per-chunk probability)
  → classify: high_conf_langs = {lang : ∃ chunk prob ≥ T}
      |high_conf_langs| ≥ 2 → CONFIDENT MULTILINGUAL {gl,es,fr}
      == 1 → single   |   == 0 → confused → tag (unchanged)
  → auto-record: audio_lang_verifications
        {lang_code=plurality, lang_class='multi', lang_codes=[gl,es,fr],
         source='auto-high-conf-multi'}   (suspect suppressed)
  → coverage row: 🌐 gl·es·fr badge (not ⚠ suspect); glance lane
  → subgen override: SUPPRESSED for multi/zxx
```

## Error handling

- Missing per-chunk `probability` field → detection degrades gracefully to today's language-count verdict (no multilingual classification, no crash). Surfaced by the feasibility gate.
- Malformed `lang_codes` JSON on read → treat as single (`lang_code`), log, don't crash.
- `zxx` or multilingual with no override → subgen simply per-chunk detects; no failure path.

## Testing

- **Detection rule** (unit, synthetic chunk fixtures with hand-set probabilities): 3×high-conf distinct → multilingual {a,b,c}; 3×low-conf distinct → confused → tag; 1×high-conf + 2×low-conf noise → single; 2×high-conf same lang → single (unanimous, unchanged).
- **Parser:** `parse_robust_detect` captures `probability`; absent field → graceful single/confused.
- **Store round-trip:** `lang_class`/`lang_codes` written and read back; singular `lang_code` still populated for multi; migration applies cleanly to an existing DB.
- **Coverage:** a confident-multilingual file surfaces `multilingual` (not `suspect`); appears in the glance lane.
- **Override suppression:** multilingual + `zxx` files do not forward `audio_language_override`.
- **`zxx`:** selectable via `/api/languages`; storing `zxx` suppresses suspect + skips override.
- **Regression:** existing single-language detection/coverage/UI tests stay green (the rule only adds a branch ahead of the unchanged path).

## Acceptance

1. *The Beasts*-shaped input (3 high-confidence chunks, 3 distinct languages) is auto-recorded `multilingual {gl,es,fr}`, badge `🌐 gl·es·fr`, **not** `suspect`, and forwards no override.
2. A genuinely-confused file (low-confidence disagreement) still falls back to the tag — no false multilingual.
3. A user can mark a file `zxx`; it stores + displays correctly and skips the override.
4. Existing single-language behaviour is unchanged (full suite green; ruff clean).

## Deferred (follow-ons, not this slice)

- **Empirical `T` tuning** — once per-chunk probabilities have accrued from normal detection (this feature starts persisting them), sweep the real distribution to pick `T` — same method as the #359 re-timer sweep, **no extra GPU** (data captured during ordinary runs).
- **Series-level multilingual intent** (`series_lang_intent` multi) — a whole-series "this show is multilingual" declaration.
- **Coverage/sub-needed language-awareness** and **per-segment language tracking** — explicitly out of scope (the "+ coverage awareness" / "+ per-segment" ambitions declined in brainstorming); would be their own epics, adjacent to the #161 multilingual JP/EN roadmap.

## Risk tier

**Tier-1/2** — touches the data model (additive migration), the detection classifier, and the coverage/UI display. The detection-rule change sits ahead of the unchanged single-language path; the migration is additive (no PK change, no backfill). Multi-lens pre-merge review per the subarr review program; the additive migration + the "singular `lang_code` always populated" invariant are the spots to scrutinise.
