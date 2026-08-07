# #171 Phase 2 — three-arm quality study

**Verdict: the gate FAILS. Do not adopt `refactor/drop-stable-ts` yet.**

Measured 2026-08-07/08 against a gate registered in the plan **before any arm was run**.

---

## Result

35 clips, post-retime, pooled by cue (not averaged per file).

| arm | build | cues | pre-retime >25 CPS | post-retime >25 CPS | overlaps |
|---|---|---|---|---|---|
| 1 — upstream today | `main` @ 2026.07.3, unpatched | 3179 | 33.34% | **13.43%** | 0 |
| 2 — new segmenter | `drop-stable-ts` @ 7d43d9a, unpatched | 2150 | 15.53% | **5.58%** | 0 |
| 3 — shipped (ours) | `2026.07.3-r1` | 1638 | 9.16% | **2.81%** | 0 |

**Gate (arm 2 vs arm 3, post-retime):** 2.81% → 5.58% = **+2.77pp against a +2.0pp tolerance. FAIL.**

**Overlap hard-fail: not triggered.** Zero overlaps in every arm.

---

## Three readings, and only one of them is "no"

**The new segmenter is a large improvement on upstream today.** 13.43% → 5.58% post-retime, less than half the CPS problem. Measured against *upstream's current state*, the branch is plainly better.

**But it is worse than what we ship.** Against arm 3 it roughly doubles the share of unreadable cues. Adoption today would cost existing users real quality, and that is what the gate protects.

**Strongpad is emphatically still earning its keep.** Arm 3 vs arm 1 was designed as a control to detect the *opposite* — the spec said a near-zero delta there would mean strongpad had quietly stopped mattering. It is 13.43% → 2.81%, a **4.8× reduction**. That question is settled in the other direction, and `CUSTOM_REGROUP` being GONE on the branch is therefore a genuine loss rather than a formality.

---

## Why the result is signal, not variance

Whisper run-to-run noise was measured per arm, not assumed.

| arm | repeat runs | byte-identical files | post-retime noise |
|---|---|---|---|
| 3 | 2 | 36 / 36 | **0.00pp** |
| 2 | 2 | 29 / 36 | **0.06pp** |

⚠️ **The design's premise was wrong in both directions, and checking mattered.**

The spec assumed "Whisper is not bit-deterministic". For **arm 3** that is false — two runs produced byte-identical output across all 36 clips (`temperature` starts at 0.0 with `beam_size: 5`, so beam search at temperature zero over identical audio on identical hardware).

But that determinism **did not transfer**. Arm 2 differs on 7 of 36 files between runs. Had the arm-3 result been generalised, the study would have claimed a zero noise floor it had not earned for the arm that actually decides the gate.

The overshoot is 0.77pp beyond tolerance against a 0.06pp floor — **46× the noise**. The failure is real.

---

## Corpus

36 clips, 240s each, 146.7 min of audio, stratified by dialogue density and balanced 9/9/9/9 across sparse / normal / dense / very_dense. Composition was fixed **before any metric existed** and is recorded in the plan.

`very_dense` is capped at 9 because the library holds only 14 such candidates and 5 are short sample files (a "Borgen episode" of 3.1 MB / 33 s, a "Bluray-1080p episode" of 60 s). All bands were capped to match rather than letting `normal` — 787 available — dominate a study about behaviour under load.

⚠️ **Band labels describe the source file, not the extracted window.** A fixed 300s offset can land in an action sequence, so "Army of Thieves" yields 13 cues despite its file banding `very_dense`. Identical across arms, so the comparison holds.

---

## Held constant across arms

Same clips, same 16 kHz mono audio, same `WHISPER_MODEL=large-v3`, `COMPUTE_TYPE=float16`, `TRANSCRIBE_DEVICE=cuda`, `CONCURRENT_TRANSCRIPTIONS=1`, and the identical `SUBGEN_KWARGS`. `task=transcribe` and `output=srt` were pinned **per request** rather than inherited, because the shipped default is `translate` and an arm that translated would be a different job wearing the same label.

`CUSTOM_REGROUP` was cleared on arms 1 and 2 — it is our lever, and leaving it set would contaminate the vanilla arms.

**Arms 1 and 2 were built from our own Dockerfile with pristine upstream trees staged**, so the CUDA base and every dependency match arm 3 and only `subgen.py` differs. Each image's `subgen.py` blob hash was verified against its pinned commit after building, because a green build is not proof the intended tree went in:

- arm 1 → `dbb906891b40aab95f764045ff8336f22b6cbedd` = `f38dcaa8:subgen.py`
- arm 2 → `6d6add6617d1c5a2b38b5ce1d7cfe2c386ca2121` = `7d43d9a:subgen.py`

No arm ran locally-modified code. Upstream shipped the `fw_kwargs` fix (McCloudS/subgen#355, commit `2145286`) before arm 2 ran, so the local patch the plan had authorised was never needed. Verified directly: arm 2 transcribed cleanly with both `condition_on_previous_text` and `vad_filter` present, the exact config that previously raised `TypeError`.

---

## Excluded clip, and a bug it exposed

`sparse_05_Steppenwolf (2024)` is excluded from all arms so the comparison runs over an identical 35.

**Arm 1 crashes on it, reproducibly.** Arm 2 and arm 3 handle the same clip without incident.

The crash is **not in subgen and not in stable-ts** — it is in faster-whisper:

```
File "/subgen/subgen.py", line 1089, in asr_task_worker
  result = model.transcribe(...)
File "stable_whisper/whisper_word_level/faster_whisper.py", line 155, in faster_transcribe
File "stable_whisper/non_whisper/transcribe.py", line 343, in transcribe_any
File "faster_whisper/transcribe.py", line 1590, in add_word_timestamps
File "faster_whisper/transcribe.py", line 1744, in find_alignment
  jump_times = time_indices[jumps] / self.tokens_per_second
IndexError: boolean index did not match indexed array along axis 0;
size of axis is 0 but size of corresponding boolean axis is 1
```

It is the **word-timestamps alignment path**, reached through stable-ts's `transcribe_any` wrapper.

⚠️ **Deliberately NOT reported upstream. The trigger is not characterised.** Three
hypotheses were tested and two are refuted:

| hypothesis | test | result |
|---|---|---|
| corrupt or unreadable file | arms 2 and 3 transcribe it fine | **refuted** |
| `CUSTOM_REGROUP` / regroup path | arm 3's own image with `CUSTOM_REGROUP` cleared | **refuted** — still succeeds |
| near-silence (clip is -39.7 dB mean) | four *quieter* clips, down to -47.8 dB, all pass on arm 1 | **refuted** |

That leaves one reproducing file out of 36, a stack trace in a third-party library, and
no account of what actually triggers it. Filing that would be an anecdote, not a bug
report — and it would go to the wrong project. Recorded here; revisit if a second
instance appears.

---

## What would change the verdict

The failure is 0.77pp. It is not a rout, and the gate is about the retimer, not the segmenter:

- **Strengthen the retimer.** Arm 2 pre-retime is 15.53% and post-retime 5.58%, so the retimer already removes ~64% of the problem. Closing 0.77pp requires it to reach ~71%. That is the cheapest route and it is #171-immune by design.
- **Re-run when the branch moves.** The branch is active; it is 4 commits newer than when Phase 1 audited it, and one of those was our own bug fix.
- **Do not relax the gate.** It was registered before any number was seen, and moving it now would forfeit the only thing that makes this study evidence.

---

## Reproducing

```bash
python scripts/phase2_corpus.py --per-band 9 --clip-seconds 240 --root //10.10.10.2/share/Media
python scripts/phase2_run_arm.py --arm arm3-run1 --url http://localhost:9008
```

Arm SRTs are committed under `study/out/`. `study/clips` and `study/audio` are gitignored — 3.6 GB of regenerable media rebuilt from `manifest.json`.
