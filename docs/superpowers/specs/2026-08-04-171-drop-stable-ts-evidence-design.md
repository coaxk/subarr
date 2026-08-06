# #171 drop-stable-ts: evidence before decision

**Status:** design approved 2026-08-04, not yet executed
**Issue:** [coaxk/subarr#171](https://github.com/coaxk/subarr/issues/171)

## Why this exists

Upstream `McCloudS/subgen` has a live branch `refactor/drop-stable-ts` that removes
`stable-ts` and replaces it with a direct faster-whisper pipeline plus a
Netflix-style segmenter. It deletes `CUSTOM_REGROUP` and `WORD_LEVEL_HIGHLIGHT`.
When it merges, our patch stack has to move.

**This spec does not decide whether to adopt it.** It defines the evidence that
would decide, and the order to gather it in. That ordering matters: the cheap
phase can veto the expensive one.

## What was already verified (2026-08-04)

Facts established before designing, because the issue's framing turned out to
overstate the risk:

| Claim in #171 | Reality |
|---|---|
| "`CUSTOM_REGROUP` is the knob our entire regroup investment rides on" | subarr's source has **3 references, all comments** — none functional. The arena sweeps `SUBGEN_KWARGS`, not regroup. The regroup tuning lives baked in our subgen image. |
| Branch is 14 commits (8 Jun) | **44 commits** ahead of main, actively developed (latest work is refining the segmenter's gap-split heuristics) |
| Replacement is knob-less | Exposes `MAX_LINE_LENGTH=42`, `GAP_SPLIT_SECS=0.4`, `MAX_SEGMENT_SECS=5.0` — semantically meaningful subtitle parameters, and **sweepable by the Tuning Lab** in a way a stable-ts DSL string never was |

Also established:
- On the branch: `CUSTOM_REGROUP`, `WORD_LEVEL_HIGHLIGHT`, `stable_whisper` are all **0 occurrences**. On current main they are 5 / — / 4. The knobs are gone, not renamed.
- Not merged yet: `stable_whisper` still appears 4x in upstream `main`. The window is open.
- Migration bill, measured by applying each patch **independently** against the pristine branch (not sequentially, so this is not cascade inflation): **34 patches, 4 apply clean, 30 conflict.** Six of the 30 are trivial `bump-patch-rev` one-liners, leaving ~24 substantive.
- The branch is buildable: `stable-ts` is gone from `requirements.txt`, and our `docker/Dockerfile` builds from a staged tree, so a vanilla-branch image needs **no porting**.
- `subtitle_retime.py` was already written segmenter-agnostic — its docstring names both "stable-ts regroup" and "the drop-stable-ts Netflix segmenter".
- The branch also *adds* `TRANSCRIBE_BACKEND` with whisper.cpp support. This is not purely a subtraction.

## Decision criterion (agreed, pre-registered)

**Adopt upstream unless the new base fails a pre-registered quality gate — with a
capability veto.** (Gate defined precisely in Phase 2's decision rule.)

We are a thin patch layer by design, and every sync where upstream absorbed our
changes has been cheaper than diverging. So the default is to follow. But if a
capability users depend on has nowhere to live on the new base, that outweighs a
small quality delta, because it removes a mechanism rather than degrading an
output.

Pre-registering this so the result is not rationalised after the numbers land.

## Phase 1 — capability survival audit

**No GPU, no porting, no image build.** Reading and grepping against the branch.

### Unit of analysis

**Capabilities, not patches.** subarr negotiates against the flags our `/queue`
advertises; those flags are the actual contract. A patch conflicting is
uninteresting (context drift is normal sync work). A capability having nowhere
to live is the veto.

The 16 flags live subgen currently advertises:

```
asr_arena · asr_detected_language · asr_vanilla_base · async_config
audio_language_override · concurrent_transcriptions · curated_language_prompts
detect_language_track · ignore_forced_subtitles · per_request_kwargs
per_request_task · queue_cancel · request_ignore_forced
robust_language_detection · runtime_config · safe_decode_preset
```

### Method

For each capability: identify the patch(es) providing it, extract the seam each
attaches to (the function or dispatch point it modifies), and determine whether
that seam exists on `refactor/drop-stable-ts`. Classify:

- **NATIVE** — upstream now does it; delete the patch. (Precedent: `0033` became
  NATIVE on 2026.07.2 after upstream adopted our `SKIP_STARTUP_SCAN` fix.)
- **PORTABLE** — the seam exists, the patch needs re-contexting. Ordinary sync work.
- **GONE** — no equivalent seam in any form.

### The mechanical pass is a first cut, not the answer

Grepping for an anchor symbol tells you a *name* disappeared, not that a
*capability* did — it may survive renamed or relocated. Anything landing in GONE
gets a manual second pass hunting for an equivalent seam. **Only GONE-after-
verification counts toward the veto.**

This is the same discipline that kept the 30-conflict number honest: the first
sequential measurement would have implied cascade, so it was re-run
independently to separate real conflicts from downstream victims.

### Veto condition

If any of **`per_request_kwargs`**, **`asr_arena`**, or **`runtime_config`** is
GONE-after-verification, Phase 2 does not run. Those three are the mechanism
behind the arena, the Tuning Lab, and the #124 federated-tuning direction. Losing
them is a different decision than losing output quality.

### Output

`docs/drop-stable-ts-capability-audit-<date>.md`: a table of
capability → verdict → evidence, plus an explicit verdict line naming whether
the veto fired.

## Phase 2 — quality study, three arms

Runs only if Phase 1 clears the veto.

### Arms

| Arm | Build | Isolates |
|---|---|---|
| 1. vanilla-old | upstream `main` (2026.07.3), unpatched | what upstream produces today |
| 2. vanilla-new | `refactor/drop-stable-ts`, unpatched | **the segmenter change alone** (vs arm 1) |
| 3. patched-old | our shipped `2026.07.3-r1` | ship reference — what users get now |

Arm 1 vs 2 is the honest segmenter comparison, with our tuning held out of it.
Arm 3 vs 1 is a **built-in control**: it shows what our patches actually buy. If
that delta is ~zero, that is a finding in its own right and worth reporting.

Comparing arm 2 against arm 3 alone would conflate "new segmenter" with "lost our
tuning" and make the new base look worse than it is. That is why there are three
arms and not two.

### Stated prediction (shapes the metrics)

Both bases use faster-whisper for the ASR itself; the branch changes
*segmentation*, not transcription. So the **words should be near-identical across
arms**, and the difference should live entirely in how they are grouped into cues.

Consequence: the semantic judge is mostly a **control**, and structural metrics
carry the signal. **If semantic scores move materially, something other than the
segmenter changed and the study needs re-scoping before its numbers are trusted.**

### Metrics

Reuses the #359 retimer-validation toolkit, which measured exactly these on 1801
real subs:

- CPS distribution, specifically share of cues over the 25 CPS readability threshold
- sub-second "flash" cues
- overlap count
- line length distribution
- cue count and duration

Plus the LaBSE reference-free QE judge (validated, ρ=0.727) as the semantic control.

### The confound that could flip the result

Our retimer runs **downstream of subgen** and is already segmenter-agnostic. So
every metric is captured **pre-retime and post-retime**. If the retimer already
repairs the new segmenter's weaknesses, the delta users would actually see is
much smaller than the raw delta, and adoption gets easier. Measuring only raw
segmenter output would overstate the cost of adopting.

### Corpus

Real clips from the live library, stratified by dialogue density (dialogue-heavy /
sparse / music-heavy / fast-speech). Identical clips, model, and compute type
across all three arms. Clip count is set by the noise measurement in the decision rule below, not picked
arbitrarily — the same way the LID study sized its 1706 windows.

### Decision rule

"Materially worse" is defined here rather than left to judgement, because a
pre-registered rule with an undefined threshold is not pre-registered at all.

**Primary gating metric:** share of cues exceeding 25 CPS, measured **post-retime**.
This is the readability threshold #359 already validated against, where the
retimer moved 22.9% → 5.2% on an 1801-subtitle corpus.

- **Not materially worse** = within **+2 percentage points** of patched-old on
  that share. (Against a ~5% baseline that allows a meaningful regression to pass
  while catching anything approaching a return to pre-retimer readability.)
- **Hard fail, regardless of everything else:** any increase in overlapping cues.
  The retimer guarantees zero new overlaps; a segmenter that produces them is a
  correctness regression, not a preference.

**Reported but not gating:** sub-second flash cues, line-length distribution, cue
count and duration, and the semantic QE control. These inform the write-up and
would motivate tuning, but do not by themselves block adoption.

**Sizing the corpus against noise.** Whisper is not bit-deterministic, so before
comparing arms, run **arm 3 twice over the same clips** to measure run-to-run
variance in the primary metric. The corpus must be large enough that the +2pp
threshold sits clearly outside that observed noise band. If it does not, the
corpus is too small and the comparison is not yet meaningful — grow it rather
than reporting a number that cannot support the claim.

1. If vanilla-new's **post-retime** primary metric is within +2pp of patched-old
   and adds no overlaps → **adopt**, and the migration becomes ordinary sync work.
2. If it fails either gate → before concluding, check whether `MAX_LINE_LENGTH`,
   `GAP_SPLIT_SECS`, `MAX_SEGMENT_SECS` can close the gap. They are sweepable and
   tuning them is precisely what the Tuning Lab exists for. A first-pass loss that
   tuning recovers is not a reason to fork.
3. Only if tuned vanilla-new still fails the same gates does forking or pinning
   come back on the table — and that would be a fresh decision with this evidence in hand.

## Explicit non-goals

- **Not porting the 24 substantive patches.** The whole point of the phase order
  is to avoid paying migration cost before deciding. Phase 2 uses vanilla builds.
- **Not deciding the fork/pin question now.** This spec produces evidence. If the
  evidence says "clearly worse even tuned", that is a new brainstorm.
- **Not touching upstream.** No PRs to `McCloudS/subgen` as part of this.

## Risks

- **The branch is a moving target** — 44 commits and still active. Both phases
  must record the exact branch SHA measured (`7997624` at time of writing), and a
  re-measure is cheap enough to redo if it moves materially before a decision.
- **Whisper is not bit-deterministic**, so identical clips can produce slightly
  different words run to run. Structural metrics are aggregate distributions, which
  is robust to that, but per-cue diffing would not be — do not build the conclusion
  on individual cue comparisons.
- **`rebase-test`'s weekly `drop-stable-ts-preview` writes to `$GITHUB_STEP_SUMMARY`**,
  which has no clean API and is web-UI only. That is very likely why the preview
  ran for eight weeks unread. If this migration proceeds, that job should emit its
  bill somewhere readable, or it will keep being invisible.
