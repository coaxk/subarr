# Forced-Segment LID Threshold Validation — Results (2026-07-14)

Study for #364. Validates `lid_min_confidence` / `lid_max_english_prob` (live
defaults 0.5 / 0.25) against real, correctly-labelled audio. Tooling:
`src/subarr/lid_tune.py` (pure sweep/metrics, reuses `window_is_foreign`) +
`scripts/lid_tune.py` (live extraction). Read-only; no library files modified.

## Corpus

1,706 fifteen-second speech windows, VAD-segmented and classified once each by
silero-lang95:

- **572 English windows** (negative class) from the most-populated English shows.
- **1,134 foreign windows** (positive class) across 10 languages, drawn only from
  subarr's own `audio_lang_audit` rows where `status = 'agrees'` and the track tag
  equals the detected language. This excludes the mislabel / bilingual /
  multitrack / confused files by construction, so the labels are gold-standard
  (subarr both saw the tag and heard the language).

Foreign languages: fr, es, ja, de, it, nl, ru, sv, no, hr.

## Sweep (per-window)

| min_conf | max_en | fp_rate | recall |
|---|---|---|---|
| 0.40 | 0.25 | 0.091 | 0.832 |
| **0.50** | **0.25** | **0.079** | **0.780** |
| 0.50 | 0.15 | 0.066 | 0.775 |
| 0.60 | 0.25 | 0.065 | 0.710 |
| 0.70 | 0.10 | 0.049 | 0.643 |
| 0.80 | 0.10 | 0.035 | 0.589 |

**Default (0.5 / 0.25): false-positive 7.9% (45/572), recall 78.0% (884/1134).**

## Per-language recall at the default

| lang | recall |
|---|---|
| ru | 91.5% |
| es | 84.4% |
| sv | 82.9% |
| de | 79.3% |
| ja | 76.7% |
| fr | 74.9% |
| it | 72.9% |
| nl | 68.6% |
| no | 66.7% |
| hr | 41.7% (n=12) |

## Findings

1. **`lid_max_english_prob` is nearly inert.** Across the full sweep, varying it
   0.10–0.40 barely moves fp/recall at a fixed confidence floor. `lid_min_confidence`
   is the dominant lever. (Same "one param carries the signal" pattern as the
   multilingual-T and re-timer sweeps.)
2. **The min_conf trade-off is smooth**, no knee. Lower gains recall at
   proportionally more false positives; higher sheds recall fast.
3. **Per-window fp_rate overstates the user-facing rate.** A spurious
   `.forced.en.srt` is only written when false-positive windows survive
   span-assembly (merge + minimum-duration + mostly-foreign bail). Lone
   false-positive windows are filtered before any sub is produced.

## Recommendation

Keep `lid_min_confidence = 0.5` and `lid_max_english_prob = 0.25` — validated as a
sound balance for an opt-in, off-by-default feature. `max_en` may optionally
tighten to 0.15 for a small strictly-beneficial false-positive reduction
(7.9% → 6.6%, −0.5pp recall), but the effect is minor.

**Follow-up before any aggressive retune:** measure the per-*file* spurious-subtitle
rate by running the actual span-assembly over the English corpus, rather than the
raw per-window flag. That is the number the user actually experiences.
