# LID Threshold Validation — Design

**Issue:** #364 (forced-segment epic — "finish" follow-on).
**Date:** 2026-07-14
**Status:** approved for planning (corpus confirmed against the live library).

## Goal

The forced-segment local LID flags a 15s window as foreign iff `top_lang` is
non-English **and** `top_prob ≥ lid_min_confidence` (0.5) **and** `english_prob ≤
lid_max_english_prob` (0.25). Those two thresholds are spike-derived and "not yet
validated on a broad set" (slice-2 notes). This study measures how 0.5/0.25
actually perform on a large, real, multi-language corpus drawn from the user's own
TV library, and recommends whether to keep or retune them.

**The metric that matters:** a false positive on true-English audio produces a
bogus `.forced.en.srt` on an English file — visible junk. So the dominant number
is **false-positive rate on true-English windows**, traded against **recall on
true-foreign windows** (and per-language recall). The current defaults are
deliberately over-flag-biased; this quantifies the real cost of that bias.

## Grounding (all verified in the running `subarr-next` container)

- **VAD model present** (`vad.vad_available()` True); **LID model pulled**
  (`lid.ensure_available()` → True).
- **Corpus source = Sonarr** (subarr's own configured connection): 1,602 series,
  918 English + 684 non-English across ~35 languages; **256 foreign shows that are
  populated** (episodeFileCount > 0). French 129, Spanish 100, Italian 78, German
  59, Polish 40, Dutch 39, Danish 32, Swedish 31, Norwegian 24, Japanese 20, plus
  Serbian/Finnish/Russian/Korean/Hebrew/Turkish and more.
- **Path translation:** Sonarr reports `/data/Media/...` (`ARR_PATH_PREFIX`);
  subarr mounts the media at `/media/library/...` (`SUBARR_MEDIA_ROOT`). Strip the
  arr prefix, prepend the media root. Verified: a real 737 MB file resolves and
  `os.path.exists` is True.
- **Reads are fast** (10GbE NAS, files direct — NOT debrid-mounted): `ffprobe`
  0.15s. Audio-track language tags are reliable ground truth (Solitary Gourmet's
  single stream is tagged `jpn`, matching Sonarr's Japanese).

## Architecture

Mirror the `retime_tune` / `multilang_tune` precedent: a pure, unit-tested core in
`src/subarr/lid_tune.py` + a live-extraction CLI in `scripts/lid_tune.py`. The core
reuses the **production predicate** `forced_segment.window_is_foreign` so the sweep
can never diverge from live behaviour.

### Ground-truth labelling (avoid the dub trap)

A foreign file may carry an English dub track. Feeding the LID the wrong track
would mislabel the window. So per file:
1. `ffprobe` the audio streams + their language tags.
2. `select_audio_stream(streams, expected_iso)` picks the stream whose tag matches
   the expected language; if exactly one stream, use it; if ambiguous (multiple
   streams, none matching the expected language), **skip the file** to keep labels
   clean (recorded as skipped, not silently dropped).
3. `expected_iso` comes from Sonarr's `originalLanguage` name mapped to ISO-639-2
   via subarr's existing language table; English shows are labelled `english`.

### Window extraction (production-faithful, bounded)

Per selected file+stream: extract a bounded sample of the *selected* stream to a
16 kHz mono wav (e.g. `ffmpeg -ss <offset> -t <secs> -map 0:a:<idx> -ac 1 -ar 16000`,
sampling a mid-file region to skip credits/theme music), then silero-VAD it,
`assemble_windows(lid_window_s=15)`, take up to N windows, and run
`lid.classify_samples` on each window's samples. This is exactly the production
`LocalLidBackend` path, but it captures the **raw** verdict
`(top_lang, top_prob, english_prob)` per window instead of the thresholded bool.

### Sweep + metrics (pure core)

A window record: `{truth: "english"|"foreign", lang: <iso>, top_lang, top_prob, english_prob}`.

- `evaluate(records, min_conf, max_en) -> ThresholdCell` — builds a
  `ForcedSegmentParams(lid_min_confidence=min_conf, lid_max_english_prob=max_en)`
  and applies `window_is_foreign` to each record, counting false positives
  (english flagged foreign) and true positives (foreign flagged foreign).
  `ThresholdCell` exposes `fp_rate` and `recall`.
- `sweep(records, conf_grid, en_grid) -> list[ThresholdCell]`.
- `per_language_recall(records, min_conf, max_en) -> dict[iso, (hit, total)]`.
- `recommend(cells, *, max_fp_rate) -> ThresholdCell` — the highest-recall cell
  whose `fp_rate <= max_fp_rate` (FP is the costly error, so it is the constraint;
  recall is maximised within budget). Ties broken toward higher `min_conf` / lower
  `max_en` (the more conservative cell).
- `format_report(...)` — corpus summary, the sweep grid (fp_rate vs recall),
  per-language recall at the current default and the recommendation.

### Corpus scale (read cost is negligible)

- **English negatives:** ~60 shows × 1 episode, up to ~12 windows each — a large
  negative set for a tight FP-rate estimate.
- **Foreign positives:** ~3 shows each across every language with content (~15
  languages), 1 episode each, up to ~12 windows.
- ≈ ~1,500–2,500 labelled windows total. Extraction parallelised across workers
  (10GbE NAS, resources unconstrained). Raw records persisted to JSON so the sweep
  re-runs offline for free.

## Deliverable

- `src/subarr/lid_tune.py` (pure core) + `scripts/lid_tune.py` (CLI: build corpus
  from Sonarr → extract → persist records → sweep → write report).
- A written report (corpus stats, sweep grid, per-language recall, recommendation).
- A defaults change to `lid_min_confidence` / `lid_max_english_prob` **only if** the
  data beats 0.5/0.25 (its own TDD + config change + CHANGELOG); otherwise a
  documented confirmation that 0.5/0.25 hold.

## Testing

- **Pure core** (`tests/test_lid_tune.py`): synthetic records pin `evaluate`
  (fp/tp counts, fp_rate, recall), `sweep` (grid shape), `per_language_recall`,
  `recommend` (respects the fp budget; conservative tie-break),
  `select_audio_stream` (tag match / single-stream / ambiguous→skip), and
  `format_report`. No file/network I/O in these tests.
- **Harness** (`scripts/lid_tune.py`): thin glue over already-tested primitives
  (`vad`, `forced_segment.assemble_windows`, `lid.classify_samples`,
  path-translation); validated by the live run, not the unit suite.

## Out of scope

Any change to the detector/gate/output; the slice-3 primary-lang generalization
(deferred). This study only measures the two thresholds and, at most, retunes their
defaults.
