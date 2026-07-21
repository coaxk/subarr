# LID Threshold Validation — Implementation Plan

> Executes the design in `docs/superpowers/specs/2026-07-14-lid-threshold-validation-design.md`.
> Pure core is TDD'd inline (offline analysis tool, not production-behaviour code).
> Any defaults change that results goes through full TDD + review separately.

**Goal:** Measure how `lid_min_confidence`/`lid_max_english_prob` (0.5/0.25) perform on a large real multi-language corpus from the user's TV library; recommend keep-or-retune.

---

### Task 1: Pure core `src/subarr/lid_tune.py` (TDD)

**Files:** Create `src/subarr/lid_tune.py`; Test `tests/test_lid_tune.py`.

Reuses the production predicate `forced_segment.window_is_foreign` so the sweep can never diverge from live behaviour.

- [ ] **Step 1 — failing tests** (`tests/test_lid_tune.py`): synthetic records pin:
  - `evaluate`: a set of english + foreign records → correct `false_positives`/`true_positives`, `fp_rate`, `recall`.
  - `sweep`: grid length = len(confs) × len(ens).
  - `per_language_recall`: groups foreign records by `lang`, (hit,total) per language.
  - `recommend`: returns the highest-recall cell with `fp_rate <= max_fp_rate`; None if none qualify; conservative tie-break (higher min_conf, then lower max_en).
  - `select_audio_stream`: exact tag match wins; single stream → that stream; ambiguous (multiple, none matching) → None.
- [ ] **Step 2 — run, confirm fail.** `python -m pytest tests/test_lid_tune.py -q`
- [ ] **Step 3 — implement** the functions per the spec's "Sweep + metrics" section:
  `conf_grid`, `en_grid`, `ThresholdCell` (frozen dataclass with `fp_rate`/`recall` properties), `_flag` (builds `ForcedSegmentParams(lid_min_confidence=…, lid_max_english_prob=…)` and calls `window_is_foreign`), `evaluate`, `sweep`, `per_language_recall`, `recommend`, `select_audio_stream(streams, expected_tags: set[str])`, `format_report`.
- [ ] **Step 4 — run, confirm pass.** Full file green.
- [ ] **Step 5 — lint** `ruff check src/subarr/lid_tune.py && ruff format src/subarr/lid_tune.py tests/test_lid_tune.py` then **commit**.

### Task 2: Extraction CLI `scripts/lid_tune.py`

**Files:** Create `scripts/lid_tune.py` (glue; run inside the `subarr-next` container).

Pure helpers already tested in Task 1; this wires them to live data:
- `build_corpus(sonarr) `: query `/api/v3/series`; sample ~60 English + ~3 shows per non-English language that has files; for each, one episodefile via `/api/v3/episodefile`; translate arr→fs path (strip `ARR_PATH_PREFIX`, prepend `SUBARR_MEDIA_ROOT`); map Sonarr `originalLanguage` name → acceptable ISO tag set.
- `extract_records(fs_path, truth, lang, expected_tags)`: `ffprobe` audio streams → `select_audio_stream`; if None, record skip. Else `ffmpeg -ss <mid> -t <secs> -map 0:a:<idx> -ac 1 -ar 16000` to a temp wav; silero-VAD it; `assemble_windows(15s)`; up to N windows; `lid.classify_samples` per window → `{truth, lang, top_lang, top_prob, english_prob}`.
- `main`: build corpus → parallel extract (thread pool; NAS is fast) → persist records JSON → `sweep` + `per_language_recall` + `recommend(max_fp_rate=…)` → write report.
- [ ] Commit the script (runs live; validated by the run, not the unit suite).

### Task 3: Live run + report

- [ ] Copy/run `scripts/lid_tune.py` inside `subarr-next` (has models + NAS + Sonarr).
- [ ] Persist raw records + the report to the repo (`docs/` or an analysis note).
- [ ] Interpret: FP-rate at 0.5/0.25, per-language recall, the recommendation.

### Task 4 (conditional): Retune defaults

- [ ] **Only if** the sweep beats 0.5/0.25: change the defaults in `config.py` (+ `ForcedSegmentParams`), TDD the change, CHANGELOG, and run through the normal review. Otherwise: document that 0.5/0.25 hold.
