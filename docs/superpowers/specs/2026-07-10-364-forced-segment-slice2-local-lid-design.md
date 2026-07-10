# #364 Slice 2 — Local windowed LID (design)

**Status:** approved design, pre-plan. Part of the #364 forced-segment epic (slice 1 shipped in PR #421). Feature stays behind `SUBARR_FORCED_SEGMENT_ENABLED`, OFF by default.

**Goal:** Replace slice 1's per-utterance subgen `/asr` language-ID (a full Whisper transcribe per utterance) with a fast, local, no-torch spoken-LID pass, so forced-segment scanning is affordable at library scale. Pure subarr change; the subgen fork is untouched. Slice 1's subgen LID remains the universal fallback.

---

## 1. Why this slice (and what the investigation settled)

Slice 1's detector clips each speech utterance and uploads it to subgen for language-ID. Because subgen's *cheap* language detector (`/detect_language_robust`, encoder-only `model.detect_language`) is **path-only**, slice 1 had to use the *upload-capable but expensive* `/asr task=transcribe` — a full transcription per utterance just to read the detected language. A live smoke measured ~2s warm per clip after a one-time cold start; across hundreds of utterances in a film that is minutes of GPU per file. That cost is the whole reason to enable this only opt-in, and the whole reason for slice 2.

Before committing to a local model we investigated subgen's declared/roadmap changes (the user's prompt: "don't build what upstream is about to give us"). Findings:

- The only recently landed subgen API change is the **OpenAI-compatible** `POST /v1/audio/transcriptions` / `/v1/audio/translations` (upstream #333). Both are full transcribe/translate; neither is a cheap language check. They do **not** change slice 2's cost problem.
- subgen already owns both halves of a cheap upload-LID — encoder-only `model.detect_language` **and** upload handling — but they are wired to *different* endpoints (`/detect_language_robust` = cheap+path-only; `POST /detect-language` = upload but still decodes ~30s). No endpoint combines "cheap detect + upload," and none is declared.

Conclusion: nothing upstream obviates a local model. A fork patch exposing cheap upload-LID was considered and rejected for this slice: it keeps N per-window subgen round-trips (network + single-model GPU queue contention) and needs a cross-repo fork release (the fork also has current upstream-drift debt). The epic's original choice — a local model that eliminates per-utterance round-trips entirely — stands.

## 2. Model decision (feasibility-spiked, not assumed)

Requirement from the codebase's ML philosophy: **no torch.** `[vad]` and `[qe-onnx]` deliberately run ONNX models on bare `onnxruntime + numpy` (torch is 0.8–2 GB and explicitly avoided); only the runtime is baked into the image, the model is pulled lazily.

- **VoxLingua107-ECAPA — ruled out.** It is the accuracy benchmark (107 langs, 6.7% err) but **no repo ships an ONNX export** — every candidate is SpeechBrain `.ckpt` (torch), TensorFlow `saved_model.pb`, MLX, or CoreML. Putting it on our stack means self-exporting torch→ONNX **and** hand-rolling its Fbank mel front-end in numpy: a sub-project plus an ongoing maintenance liability that contradicts the no-torch philosophy. Not worth it when translate is already the accuracy arbiter (§5).
- **silero-lang95 — chosen.** `deepghs/silero-lang95-onnx` ships `lang_classifier_95.onnx` + `lang_dict_95.json` (labels) + `lang_group_dict_95.json` (58 language groups). Same `snakers4/silero` lineage as our VAD; self-contained preprocessing (raw 16 kHz mono float32 in, like the VAD); runs on the **onnxruntime we already bake in**. Zero new runtime deps.

**Spike evidence (real audio, in the dev container):**

- Runs clean on bare onnxruntime: input `[batch, samples]` float32, output `[batch, 95]` logits (plus a `[batch, 58]` group head we ignore). **100–320 ms per clip** — far cheaper than a subgen round-trip.
- Reliable on **clear foreign dialogue ≥ ~15 s at high confidence**: on a known-German film, a clear-dialogue 15 s window returned `de` at 89%. A 15 s English clip returned English with a clear margin.
- **Unreliable on short or non-speech input**: a 3 s English clip was misclassified (English not even top-3), and battle/music segments of the German film returned low-confidence noise (`zh` 10%, `mn` 12%). The misclassifications are **low-confidence** — confidence is the load-bearing signal.
- Germanic-cluster confusion (de/nl/fy/yi) appears at medium confidence, but is **harmless**: the gate only needs "English vs not-English," and all of those are "not."

The spike is the reason the design classifies **windows, not raw utterances**, and gates hard on confidence.

## 3. Architecture

The slice-1 pipeline is unchanged except the LID stage. Data flow (opt-in, per file):

```
stat → cache check → gate (English-primary etc.)
  → silero VAD → speech utterances
  → LID BACKEND → per-utterance (lang, conf)      ← slice 2 changes ONLY this box
  → classify_utterances → assemble_foreign_spans → merge_foreign_spans   (slice-1 code, unchanged)
  → mostly-foreign? bail
  → per span: clip → subgen /asr task=translate (+return_language)
        → if detected source == primary: DROP (silero false-positive)     ← slice 2 adds this
        → else parse_srt → offset by span.start_ms → multi-cue
  → path-contained no-clobber write → cache record → aftercare note
```

**Key structural choice — the LID backend returns per-utterance verdicts, computed windowed under the hood.** slice-1 `classify_utterances` / `assemble_foreign_spans` / `merge_foreign_spans` / `is_mostly_foreign` all consume a per-utterance `list[(lang|None, conf)]`. We keep that contract so the entire downstream is reused. Only *how* those verdicts are produced changes:

- **`SubgenLidBackend` (slice 1, the fallback):** per-utterance — clip each utterance, upload to subgen `/asr`, read language. Exactly today's behaviour.
- **`LocalLidBackend` (slice 2, silero):** given the audio and the VAD utterances, assemble contiguous speech into **~15 s windows**, classify each window once with silero, and assign each utterance the verdict of the window that contains it. Zero subgen round-trips for detection.

This replaces slice-1's per-clip `lid_fn(clip, subgen_path, span)` injection with a backend that produces all per-utterance verdicts in one call: `classify(fs_path, utterances, params) -> list[(lang|None, conf)]`. `ForcedSegmentGenerator` selects the backend at construction: `LocalLidBackend` if the `[lid]` model loads, else `SubgenLidBackend`.

## 4. New module `lid.py` (mirrors `vad.py`)

Small, self-contained, lazy-loading — the same shape as `vad.py`:

- Lazy-download `lang_classifier_95.onnx` + `lang_dict_95.json` via `huggingface_hub` into the HF cache on first use; construct a module-level `onnxruntime.InferenceSession` once.
- Preprocessing: decode the window to 16 kHz mono float32 (ffmpeg is already used by `clip_audio`; reuse it) → feed raw samples.
- `classify_window(samples) -> LidResult{top_lang, top_prob, english_prob}` where probs are softmax over the 95-way logits and `top_lang` maps through `lang_dict_95` to an ISO code.
- Any import/load/inference-setup failure returns a sentinel so the generator falls back to `SubgenLidBackend`. Model load and inference run via `asyncio.to_thread` (event-loop discipline, same as VAD).

## 5. Detection rule and the translate-arbiter

**Windowing + gate (in `LocalLidBackend`).** For each ~15 s speech window, silero gives `(top_lang, top_prob, english_prob)`. The window is **foreign** iff:

- `top_lang` is not the primary language (`primary_lang`, "en" for slice 1, incl. the existing `ENGLISH_TAGS` set), AND
- `top_prob >= lid_min_confidence`, AND
- `english_prob <= lid_max_english_prob`.

Otherwise the window is primary/uncertain and **not** flagged. This inverts slice-1's blanket over-flag-on-low-confidence for the local backend, because silero's low-confidence output is noise (§2) — trusting it would flag English media. Over-flag bias is preserved *within confident verdicts* (Germanic-cluster confusions still count as foreign, which is correct).

**Decided-verdict contract (avoids double-gating).** The local backend applies the entire gate itself and emits a *decided* per-utterance `(lang, conf)`: a flagged window's utterances get `(top_lang, high_conf)`; every non-flagged window's utterances get `(primary_lang, high_conf)`. It never emits a low-confidence label. Downstream `classify_utterances` therefore sees only confident, unambiguous labels and marks foreign simply where `lang != primary` — so slice-1's `over_flag_low_confidence` low-confidence branch is inert on the local path (all gating already happened in the backend), while the subgen backend path is unchanged. An utterance that straddles two windows takes the verdict of the window it overlaps most (ties → the earlier window).

**Translate-arbiter (the safety net for silero's false positives).** slice-1 already sends each foreign span to subgen `/asr task=translate`. But a wrongly-flagged English span translated to English is indistinguishable from a correctly-translated foreign span by output text alone. subgen detects the *source* language before translating, so we request `task=translate` **with `return_language`** (subgen already computes it; slice-1's Branch-B read it the same way). If the detected source equals the primary language, the span was a silero false positive → **drop it**, emit no cues. Otherwise keep the translated multi-cue output. One subgen call per span, no extra cost.

## 6. Parameters and packaging

New `ForcedSegmentParams` fields (named, defaulted, tunable):

- `lid_window_s: float = 15.0` — classification window (the spike's reliable floor). Distinct from `min_span_s = 2.5` (the output cue-duration floor).
- `lid_min_confidence: float` — softmax floor for a foreign verdict. Default chosen to accept the `de=89%` case and reject the `zh=10%`/`mn=12%` noise (candidate ~0.5, to be validated in §7).
- `lid_max_english_prob: float` — reject a "foreign" verdict if English is still plausible. Candidate ~0.25, validated in §7.

The existing `max_utterance_s` / `overlap_stride_s` stubs remain unused (coarse-to-fine overlap refine is out of scope, §8).

Packaging: a new `[lid]` optional-dependencies group (`onnxruntime`, `numpy`, `huggingface_hub` — all already present via `[vad]`/`[qe-onnx]`, so no new image weight beyond the ~few-MB model pulled lazily on first use). The Docker image bakes the runtime; the model is never baked. No config surface changes — the feature is still gated solely by `SUBARR_FORCED_SEGMENT_ENABLED`.

## 7. Testing

TDD throughout, per the repo's discipline.

- **Unit — detector logic with a fake LID backend.** Inject deterministic `(top_lang, top_prob, english_prob)` per window and assert: windowing (utterances mapped to the right window verdict), the three-part foreign gate (confidence floor, english-prob ceiling, primary-language exclusion incl. `ENGLISH_TAGS`), and that low-confidence windows are treated as primary. Reuse slice-1's assemble/merge/mostly-foreign tests unchanged (their contract is preserved).
- **Unit — translate-arbiter.** Fake translate returns `(text, source_lang)`; assert spans with `source_lang == primary` are dropped and others emit offset multi-cue output.
- **Unit — backend selection + fallback.** `LocalLidBackend` chosen when the model loads; `SubgenLidBackend` when `lid.py` load returns the sentinel. Assert byte-for-byte fallback to slice-1 behaviour.
- **Real-model smoke (end of slice, reusing the spike harness).** Against the dev subgen/library: the English clip must stay **unflagged**; the German film at 3600 s must **flag `de`** and produce a `.forced.en.srt`; assert per-window latency stays in the sub-second range measured in the spike. The `lid_min_confidence` / `lid_max_english_prob` defaults are finalised here against a small real multilingual set (English shows that must stay clean + at least one clear foreign-dialogue scene).
- **OFF-by-default guarantee.** Assert the pipeline is byte-for-byte unchanged when `SUBARR_FORCED_SEGMENT_ENABLED` is unset (as slice 1).

## 8. Out of scope / deferred

- **Coarse-to-fine overlap refine** for long utterances straddling a language switch (`max_utterance_s`/`overlap_stride_s`). The 15 s window + confidence gate covers the common case; revisit only if validation shows missed mid-utterance switches.
- **58-way language-group head** — a possible future stability lever (classify by family), not needed for English-vs-not.
- **subgen fork patch** for cheap upload-LID — a viable alternative captured here for the record; not this slice.
- **Slice 3** — generalise `primary_lang` beyond English via the #357 audio-language model. Unchanged by this slice; the pipeline is already language-agnostic below the gate.

## 9. Risks

- **silero accuracy on real libraries.** Mitigated by: confidence + english-prob gating, VAD speech-gating (never classify non-speech), the translate-arbiter dropping false positives, and opt-in scope. Residual: a wrongly-flagged span costs one wasted translate; a missed quiet foreign line is a false negative the user can catch and (future) manually correct. The §7 real-set validation sets the thresholds.
- **Model-pull on first use.** First scan on a fresh install downloads ~a few MB; if HF is unreachable, `lid.py` returns the sentinel and the generator falls back to slice-1 subgen LID — degraded (slower) but functional, logged once.
