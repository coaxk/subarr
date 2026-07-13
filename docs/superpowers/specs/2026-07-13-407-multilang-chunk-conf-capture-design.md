# #407 Part A — Per-chunk-probability capture (design)

**Status:** approved design, pre-plan. Follow-on from #357 multilingual detection.

**Goal:** Make the per-chunk-probability corpus actually accrue during normal detection, so the multilingual chunk-confidence threshold `T` (`SUBARR_MULTILANG_CHUNK_MIN_PROB`, currently a placeholder `0.5`) can be tuned later from real data — with no GPU and no change to detection behavior.

**Non-goal (deferred to Part B):** the actual T tuning — sweeping the accrued distribution, finding the knee, and baking a new default. That is genuinely data-gated (multilingual is a rare class) and its analysis should be designed against real distribution shapes, not synthetic assumptions. This slice only makes the data exist and gives a minimal way to look at it.

---

## 1. Why (the correction that motivates this)

The issue assumed "#405 persists per-chunk confidence during normal detection, the corpus accrues for free." It doesn't. `parse_robust_detect` (arena.py) *surfaces* `chunks_conf` (an ordered `[(lang, probability)]` per Whisper chunk) in its parsed dict; `audio_audit._audit_one` reads it once to classify multilingual (`classify_high_conf_langs(chunks_conf, T)`), then discards it. Nothing writes it to any store. The audit store persists only *derived* aggregates (`languages_heard`, `n_agreeing`/`n_total`, and `lang_class`/`lang_codes` on `audio_lang_verifications`) — never the per-chunk probability *values* a T-sweep needs. So the prescribed method (read-only sweep of accrued data) has nothing to read. This slice closes that gap.

## 2. Architecture

Capture `chunks_conf` at the single richest detection source — the bulk audit walk — where it is already computed and about to be thrown away. Zero extra subgen calls, zero extra GPU, no new detection path.

```
audio_audit._audit_one:
    resp   = subgen.detect_language_robust(...)
    detect = parse_robust_detect(resp)          # already contains chunks_conf
    ...classify (unchanged, T still 0.5)...
    store.upsert(..., chunks_conf=detect.get("chunks_conf"))   # NEW: persist it
```

Detection behavior is unchanged — `T` stays `0.5`, classification is byte-for-byte identical. We only start saving a value that was already in hand.

## 3. Components

**Migration `029_audio_audit_chunks_conf.sql`** — additive, idempotent:
```sql
ALTER TABLE audio_lang_audit ADD COLUMN chunks_conf TEXT;   -- JSON [[lang, prob], ...], nullable
```
Existing rows default to NULL (no backfill; they predate capture). Payload is tiny (~3 `(lang, prob)` pairs per file at `chunks=3`), so no growth concern.

**`audio_audit_store.upsert`** — add a keyword-only `chunks_conf: list | None = None` param; JSON-serialize it (`json.dumps`, `None` stays SQL NULL) into the new column, in both the INSERT and the `ON CONFLICT ... DO UPDATE SET` clause, mirroring the existing `languages_heard`/`track_languages` handling.

**`audio_audit._audit_one`** — pass `chunks_conf=(detect or {}).get("chunks_conf")` into the existing `self._store.upsert(...)` call. One-line addition; nothing else in the audit path changes.

**Read-only inspector `multilang_tune.py` (+ `scripts/multilang_tune.py` CLI)** — mirrors `retime_tune.py`'s shape (a pure sweep over a small parameter grid + a `format_report`). Reads the accrued `chunks_conf` corpus from `audio_lang_audit` and reports, for a range of candidate `T` values:
- the distribution of per-chunk probabilities across the corpus,
- how many files would classify as multilingual (`>=2` high-conf distinct langs) at each `T`, reusing `classify_high_conf_langs`,
- per-file detail for the (few) files that flip between adjacent T values — the eyeball material for the eventual knee call.

It is a distribution/what-if report, NOT an auto-"recommend T" knee-finder (that is Part B). Read-only, CPU-only, off-app — it never runs detection, only reads stored rows.

## 4. Data shape

`chunks_conf` is stored as JSON: a list of `[lang, prob]` pairs in Whisper-chunk order, e.g. `[["gl", 0.94], ["es", 0.88], ["fr", 0.71]]`. `lang` may be `null`/`"und"`; `prob` may be `null` (malformed / pre-#396 subgen) — both handled exactly as `classify_high_conf_langs` already handles them (treated as below-threshold). The inspector reuses that function so the corpus reader and the live classifier can never diverge.

## 5. Testing (TDD)

- **Store round-trip:** `upsert(..., chunks_conf=[("gl",0.9),("es",0.8)])` then read back → column holds the JSON; `None` → SQL NULL; existing rows (no chunks_conf) read back as NULL without error.
- **Capture wiring:** `_audit_one` persists `detect["chunks_conf"]` — assert with a fake subgen/parse returning a known `chunks_conf` that the stored row carries it; and that a detect lacking `chunks_conf` stores NULL, never raises.
- **No behavior change:** existing multilingual/audit tests stay green (classification untouched; `T` still 0.5).
- **Inspector sweep (pure):** on a synthetic corpus of stored `chunks_conf` rows, assert the multilingual-count-at-T curve is monotonic non-increasing in T, that a clean 3-confident-lang row counts multilingual at low T and drops out as T exceeds its second-language prob, and that the report renders. No DB, no subgen.

## 6. Out of scope / deferred

- The actual T tuning (sweep → knee → bake a new default) — Part B, data-gated on accrued multilingual positives.
- Capturing at other `detect_language_robust` callers (on-demand verify, etc.) — audit-path-only by decision; the bulk walk is what builds a real corpus.
- Any telemetry/transmission of the corpus — this is a purely local tuning aid.
