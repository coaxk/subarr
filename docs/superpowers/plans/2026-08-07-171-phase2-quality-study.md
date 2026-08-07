# #171 Phase 2 — Three-Arm Quality Study Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decide whether to adopt upstream's `refactor/drop-stable-ts` base, by measuring whether the #359 retimer alone can carry the CPS load that strongpad plus the retimer currently carry together — against a gate registered **before** any number is seen.

**Architecture:** A study harness in `subarr` that (1) builds a stratified clip corpus from the live library, (2) runs three subgen images over identical clips, (3) captures every metric **pre-retime and post-retime**, and (4) evaluates the pre-registered gate. Metric extraction reuses `subarr.subtitle_readability` and `subarr.subtitle_retime` rather than reimplementing them. The corpus is sized empirically by measuring Whisper's own run-to-run noise first.

**Tech Stack:** Python 3.12, existing `subtitle_readability` / `subtitle_retime` modules, Docker with GPU passthrough, `ffmpeg` for clip extraction, pytest.

---

## Phase 1 outcome that authorises this

Phase 1 cleared the veto (subarr#171, 2026-08-06/07). `per_request_kwargs`, `asr_arena`, `runtime_config` are all PORTABLE. **`CUSTOM_REGROUP` is confirmed GONE** — on the branch `regroup` survives only in a comment and in `_STABLE_TS_KWARGS`, the strip-list. That is exactly why this study exists: strongpad is the source-side CPS control we lose, and arm 2 vs arm 3 asks whether the retimer covers its absence.

Full hunk census: INTACT 76 / BROKEN_BY_BRANCH 11 / CASCADE 42 of 129.

---

## Preconditions — verified 2026-08-07, re-check before any GPU run

- **GPU visible in containers**: `wsl docker exec subgen-next nvidia-smi` → `NVIDIA GeForce RTX 3060`. ⚠️ A WSL restart can leave containers GPU-blind for life while `wsl nvidia-smi` on the host keeps working. Re-probe per container, never the host.
- **NAS genuinely mounted**: `mount | grep /mnt/nas` returns a row **and** a real file read succeeds. ⚠️ The mountpoint dirs live on WSL's own disk, so `ls /mnt/nas/Media` lists content and looks healthy while serving nothing. `ls` alone is not a check.

---

## ⚠️ The metric trap, pinned before anything else

`subtitle_readability.analyze_cues` emits `cps` issues at **two** severities:

```python
MAX_CPS = 20.0        # -> severity "warn"
CRITICAL_CPS = 25.0   # -> severity "critical"
```

`ReadabilityReport.counts["cps"]` sums **both**. The gate is defined on **25** CPS.
Using `counts["cps"]` would measure the >20 threshold instead, inflating the primary
metric roughly two- to three-fold and applying the ±2pp tolerance to the wrong number.

**The primary metric must count only `kind == "cps" and severity == "critical"`.**
Task 1 exists to pin this with a test that fails if anyone reaches for `counts`.

---

## The gate — registered now, before any arm is run

**Primary:** share of cues exceeding **25 CPS**, measured **post-retime**.
Arm 2 passes if its share is within **+2 percentage points** of arm 3.

**Hard fail regardless of everything else:** any increase in overlapping cues versus
arm 3. The retimer guarantees zero new overlaps; a segmenter that produces them is
disqualified whatever its CPS looks like.

**Semantic control:** LaBSE reference-free QE (ρ=0.727). Expected to be flat — both
bases use faster-whisper for the ASR itself and the branch changes *segmentation*,
not transcription. ⚠️ **If semantic scores move materially, something other than the
segmenter changed and the study is re-scoped before its numbers are trusted.** That
is a stop condition, not a footnote.

**Corpus sizing:** run **arm 3 twice over identical clips** first. If the run-to-run
delta in the primary metric is not comfortably smaller than 2pp, the corpus is too
small and must grow. Sizing is decided by that measurement, not by taste.

---

## The arms

| Arm | Build | Isolates |
|---|---|---|
| 1. vanilla-old | upstream `main` @ 2026.07.3, unpatched | what upstream produces today |
| 2. vanilla-new | `refactor/drop-stable-ts`, unpatched | the segmenter change alone (vs arm 1) |
| 3. patched-old | our shipped `ghcr.io/coaxk/subarr-subgen:2026.07.3-r1` | ship reference, includes strongpad |

Arm 1 vs 2 is the honest segmenter comparison with our tuning held out.
Arm 3 vs 1 is a built-in control on what strongpad actually buys — **if that delta is
about zero, that is a finding in itself**: strongpad stopped earning its keep.
**Arm 2 vs arm 3 post-retime is the decision.**

---

## File Structure

| File | Responsibility |
|---|---|
| `src/subarr/study/metrics.py` (create) | Pure metric extraction from an SRT: 25-CPS share, overlap count, cue count, duration stats. No IO. |
| `src/subarr/study/corpus.py` (create) | Pure clip-selection logic: stratification into density bands. No ffmpeg. |
| `scripts/phase2_corpus.py` (create) | Thin CLI: walks the library, calls `corpus.py`, shells out to ffmpeg. |
| `scripts/phase2_run_arm.py` (create) | Thin CLI: runs one image over the clip set, writes SRTs to an arm directory. |
| `scripts/phase2_report.py` (create) | Thin CLI: loads arm outputs, applies retimer, evaluates the gate, renders the report. |
| `tests/test_phase2_metrics.py` (create) | Tests for `metrics.py`. |
| `tests/test_phase2_corpus.py` (create) | Tests for `corpus.py`. |
| `docs/171-phase2-quality-study.md` (create, generated) | The report and verdict. |

Pure logic is tested; the three CLIs are thin and do the IO. Same split that worked
in Phase 1.

---

## Task 1: The primary metric, with the warn/critical trap pinned

**Files:**
- Create: `src/subarr/study/__init__.py` (empty), `src/subarr/study/metrics.py`
- Test: `tests/test_phase2_metrics.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_phase2_metrics.py
from subarr.study.metrics import SrtMetrics, metrics_for_srt

# Cue 1: 10 chars in 10s  -> 1 CPS, fine.
# Cue 2: 60 chars in 2s   -> 30 CPS, ABOVE the 25 critical threshold.
# Cue 3: 44 chars in 2s   -> 22 CPS, above the 20 WARN threshold but BELOW 25.
SRT = """1
00:00:00,000 --> 00:00:10,000
ten chars.

2
00:00:10,000 --> 00:00:12,000
{sixty}

3
00:00:12,000 --> 00:00:14,000
{fortyfour}
""".replace("{sixty}", "x" * 60).replace("{fortyfour}", "y" * 44)


def test_only_cues_over_25_cps_count_toward_the_primary_metric():
    # The 22 CPS cue is a "warn" in ReadabilityReport.counts["cps"] and must NOT
    # be counted. Counting it would measure the 20 CPS threshold instead and
    # inflate the primary metric the gate is defined on.
    m = metrics_for_srt(SRT)
    assert m.cue_count == 3
    assert m.over_25_cps == 1
    assert m.over_25_cps_share == 1 / 3


def test_overlap_count_is_reported_separately():
    overlapping = """1
00:00:00,000 --> 00:00:05,000
first

2
00:00:03,000 --> 00:00:06,000
second
"""
    assert metrics_for_srt(overlapping).overlaps == 1


def test_empty_srt_yields_zero_share_and_does_not_divide_by_zero():
    m = metrics_for_srt("")
    assert m.cue_count == 0
    assert m.over_25_cps_share == 0.0


def test_metrics_are_a_plain_comparable_record():
    a, b = metrics_for_srt(SRT), metrics_for_srt(SRT)
    assert a == b
    assert isinstance(a, SrtMetrics)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /c/Projects/subarr && python -m pytest tests/test_phase2_metrics.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'subarr.study'`

- [ ] **Step 3: Write the minimal implementation**

```python
# src/subarr/study/metrics.py
"""Phase 2 primary metrics, extracted from an SRT.

Deliberately thin over subtitle_readability rather than a reimplementation --
the study must measure what the product measures, or it is measuring itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from subarr.subtitle_readability import analyze_srt, parse_srt


@dataclass(frozen=True)
class SrtMetrics:
    cue_count: int
    over_25_cps: int
    overlaps: int

    @property
    def over_25_cps_share(self) -> float:
        return self.over_25_cps / self.cue_count if self.cue_count else 0.0


def metrics_for_srt(text: str) -> SrtMetrics:
    """Primary metrics for one subtitle file.

    ⚠️ ``over_25_cps`` counts ONLY ``severity == "critical"`` cps issues.
    ``analyze_cues`` raises a *warn* cps issue above MAX_CPS (20.0) and a
    *critical* one above CRITICAL_CPS (25.0), and ``ReadabilityReport.counts``
    sums both. The gate is defined on 25 CPS, so counting the warns would
    measure the wrong threshold and apply the +2pp tolerance to a number two to
    three times too large.
    """
    report = analyze_srt(text)
    return SrtMetrics(
        cue_count=len(parse_srt(text)),
        over_25_cps=sum(
            1 for i in report.issues if i.kind == "cps" and i.severity == "critical"
        ),
        overlaps=sum(1 for i in report.issues if i.kind == "overlap"),
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /c/Projects/subarr && python -m pytest tests/test_phase2_metrics.py -v`
Expected: PASS, 4 passed

- [ ] **Step 4b: Prove the trap is real, then prove the test catches it**

Temporarily change `over_25_cps` to use `report.counts.get("cps", 0)` and re-run.
`test_only_cues_over_25_cps_count_toward_the_primary_metric` **must fail** with
`2 != 1`. Restore it. Report both outputs.

A guard nobody has watched fail is not a guard, and this one protects the number the
entire study turns on.

- [ ] **Step 5: Commit**

```bash
cd /c/Projects/subarr
git add src/subarr/study/__init__.py src/subarr/study/metrics.py tests/test_phase2_metrics.py
git commit -m "feat(#171): phase 2 primary metric, counting only cues over 25 CPS"
```

---

## Task 2: Pre-retime and post-retime metrics for the same file

**Why both:** our retimer runs downstream of subgen and is already segmenter-agnostic.
If it already repairs the new segmenter's weaknesses, the delta users actually see is
far smaller than the raw delta. Measuring only raw output would overstate the cost of
adopting — and the gate is defined post-retime.

**Files:**
- Modify: `src/subarr/study/metrics.py`, `tests/test_phase2_metrics.py`

- [ ] **Step 1: Write the failing test**

```python
from subarr.study.metrics import ArmSample, sample_for_srt


def test_sample_carries_both_stages_and_they_can_differ():
    # A 30 CPS cue followed by a gap the retimer can extend into.
    srt = """1
00:00:00,000 --> 00:00:02,000
{sixty}

2
00:00:20,000 --> 00:00:22,000
short
""".replace("{sixty}", "x" * 60)
    s = sample_for_srt("clip01", srt)
    assert isinstance(s, ArmSample)
    assert s.clip == "clip01"
    assert s.pre.over_25_cps == 1
    # The retimer may or may not clear it; what matters is both stages exist
    # and are measured from the SAME source text.
    assert s.post.cue_count == s.pre.cue_count


def test_post_stage_is_the_retimed_text_not_the_original():
    srt = """1
00:00:00,000 --> 00:00:02,000
{sixty}
""".replace("{sixty}", "x" * 60)
    s = sample_for_srt("c", srt)
    assert s.post_srt != s.pre_srt
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /c/Projects/subarr && python -m pytest tests/test_phase2_metrics.py -v`
Expected: FAIL with `ImportError: cannot import name 'ArmSample'`

- [ ] **Step 3: Implement**

Append to `src/subarr/study/metrics.py`:

```python
from subarr.subtitle_retime import retime_srt


@dataclass(frozen=True)
class ArmSample:
    """One clip's result for one arm, measured at both stages."""

    clip: str
    pre_srt: str
    post_srt: str
    pre: SrtMetrics
    post: SrtMetrics


def sample_for_srt(clip: str, srt_text: str) -> ArmSample:
    """Measure one subtitle file before and after the retimer.

    Both stages are required. The retimer is segmenter-agnostic and runs
    downstream of subgen, so measuring only raw output would overstate the cost
    of adopting a new segmenter -- and the gate is defined post-retime.
    """
    post = retime_srt(srt_text)
    return ArmSample(
        clip=clip,
        pre_srt=srt_text,
        post_srt=post,
        pre=metrics_for_srt(srt_text),
        post=metrics_for_srt(post),
    )
```

- [ ] **Step 4: Run to verify it passes**

Expected: PASS, 6 passed

- [ ] **Step 5: Commit**

```bash
cd /c/Projects/subarr
git add src/subarr/study/metrics.py tests/test_phase2_metrics.py
git commit -m "feat(#171): measure each clip pre-retime and post-retime"
```

---

## Task 3: Aggregate an arm, and evaluate the gate

**Files:**
- Modify: `src/subarr/study/metrics.py`, `tests/test_phase2_metrics.py`

- [ ] **Step 1: Write the failing test**

```python
from subarr.study.metrics import ArmResult, GateVerdict, aggregate, evaluate_gate


def _sample(clip, cue_count, over, overlaps):
    m = SrtMetrics(cue_count=cue_count, over_25_cps=over, overlaps=overlaps)
    return ArmSample(clip=clip, pre_srt="", post_srt="", pre=m, post=m)


def test_aggregate_pools_cues_rather_than_averaging_per_file_shares():
    # A 1-cue file and a 99-cue file must not carry equal weight -- averaging
    # per-file shares would let one short clip dominate the primary metric.
    r = aggregate("arm2", [_sample("a", 1, 1, 0), _sample("b", 99, 0, 0)])
    assert isinstance(r, ArmResult)
    assert r.cue_count == 100
    assert r.over_25_cps_share == 0.01


def test_gate_passes_when_within_two_points_and_no_new_overlaps():
    ref = aggregate("arm3", [_sample("a", 100, 5, 0)])
    cand = aggregate("arm2", [_sample("a", 100, 7, 0)])   # +2.0pp exactly
    v = evaluate_gate(candidate=cand, reference=ref)
    assert v.passed is True


def test_gate_fails_when_cps_share_exceeds_the_two_point_tolerance():
    ref = aggregate("arm3", [_sample("a", 100, 5, 0)])
    cand = aggregate("arm2", [_sample("a", 100, 8, 0)])   # +3.0pp
    assert evaluate_gate(candidate=cand, reference=ref).passed is False


def test_any_new_overlap_is_a_hard_fail_even_with_better_cps():
    # Strictly better CPS, one new overlap. The retimer guarantees zero new
    # overlaps; a segmenter that produces them is disqualified outright.
    ref = aggregate("arm3", [_sample("a", 100, 20, 0)])
    cand = aggregate("arm2", [_sample("a", 100, 1, 1)])
    v = evaluate_gate(candidate=cand, reference=ref)
    assert v.passed is False
    assert "overlap" in v.reason.lower()


def test_verdict_states_the_measured_numbers_not_just_a_boolean():
    ref = aggregate("arm3", [_sample("a", 100, 5, 0)])
    cand = aggregate("arm2", [_sample("a", 100, 7, 0)])
    v = evaluate_gate(candidate=cand, reference=ref)
    assert isinstance(v, GateVerdict)
    assert "5.0" in v.reason and "7.0" in v.reason
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL with `ImportError: cannot import name 'ArmResult'`

- [ ] **Step 3: Implement**

Append to `src/subarr/study/metrics.py`:

```python
CPS_TOLERANCE_PP = 2.0


@dataclass(frozen=True)
class ArmResult:
    arm: str
    cue_count: int
    over_25_cps: int
    overlaps: int
    clips: int

    @property
    def over_25_cps_share(self) -> float:
        return self.over_25_cps / self.cue_count if self.cue_count else 0.0


@dataclass(frozen=True)
class GateVerdict:
    passed: bool
    reason: str


def aggregate(arm: str, samples: list[ArmSample], *, stage: str = "post") -> ArmResult:
    """Pool an arm's samples. POOLS CUES, does not average per-file shares --
    a 1-cue clip and a 99-cue clip must not carry equal weight."""
    chosen = [(s.post if stage == "post" else s.pre) for s in samples]
    return ArmResult(
        arm=arm,
        cue_count=sum(m.cue_count for m in chosen),
        over_25_cps=sum(m.over_25_cps for m in chosen),
        overlaps=sum(m.overlaps for m in chosen),
        clips=len(samples),
    )


def evaluate_gate(*, candidate: ArmResult, reference: ArmResult) -> GateVerdict:
    """The pre-registered gate. Registered before any arm was run."""
    cand_pp = candidate.over_25_cps_share * 100
    ref_pp = reference.over_25_cps_share * 100
    if candidate.overlaps > reference.overlaps:
        return GateVerdict(
            False,
            f"HARD FAIL: overlaps {reference.overlaps} -> {candidate.overlaps}. "
            f"The retimer guarantees zero new overlaps; a segmenter that adds "
            f"them is disqualified regardless of CPS "
            f"({ref_pp:.1f}% -> {cand_pp:.1f}%).",
        )
    delta = cand_pp - ref_pp
    if delta > CPS_TOLERANCE_PP:
        return GateVerdict(
            False,
            f"FAIL: cues over 25 CPS {ref_pp:.1f}% -> {cand_pp:.1f}% "
            f"(+{delta:.1f}pp, tolerance +{CPS_TOLERANCE_PP:.1f}pp).",
        )
    return GateVerdict(
        True,
        f"PASS: cues over 25 CPS {ref_pp:.1f}% -> {cand_pp:.1f}% "
        f"({delta:+.1f}pp, tolerance +{CPS_TOLERANCE_PP:.1f}pp), "
        f"overlaps {reference.overlaps} -> {candidate.overlaps}.",
    )
```

- [ ] **Step 4: Run to verify it passes**

Expected: PASS, 11 passed

- [ ] **Step 5: Commit**

```bash
cd /c/Projects/subarr
git add src/subarr/study/metrics.py tests/test_phase2_metrics.py
git commit -m "feat(#171): aggregate arms and evaluate the pre-registered gate"
```

---

## Task 4: Build the corpus

**Stratify by dialogue density** — dialogue-heavy, sparse, music-heavy, fast-speech.
A corpus of only one kind measures one kind. Density is estimated from an existing
subtitle track where one is present (cues per minute), which avoids a chicken-and-egg
transcription pass just to choose clips.

**Files:**
- Create: `src/subarr/study/corpus.py`, `scripts/phase2_corpus.py`
- Test: `tests/test_phase2_corpus.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_phase2_corpus.py
from subarr.study.corpus import DensityBand, band_for_cues_per_minute, stratify


def test_bands_partition_the_range_with_no_gaps():
    assert band_for_cues_per_minute(2.0) is DensityBand.SPARSE
    assert band_for_cues_per_minute(12.0) is DensityBand.NORMAL
    assert band_for_cues_per_minute(25.0) is DensityBand.DENSE
    assert band_for_cues_per_minute(40.0) is DensityBand.VERY_DENSE


def test_stratify_takes_an_equal_quota_from_each_populated_band():
    items = (
        [("sparse%d" % i, 2.0) for i in range(10)]
        + [("dense%d" % i, 25.0) for i in range(10)]
    )
    picked = stratify(items, per_band=3)
    bands = {band_for_cues_per_minute(d) for _, d in picked}
    assert len(picked) == 6
    assert len(bands) == 2


def test_stratify_does_not_invent_items_for_an_empty_band():
    picked = stratify([("a", 2.0)], per_band=5)
    assert len(picked) == 1


def test_stratify_is_deterministic_for_a_given_input():
    items = [("c%d" % i, float(i)) for i in range(40)]
    assert stratify(items, per_band=2) == stratify(items, per_band=2)
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL with `ModuleNotFoundError: No module named 'subarr.study.corpus'`

- [ ] **Step 3: Implement**

```python
# src/subarr/study/corpus.py
"""Clip selection for the Phase 2 study.

Pure logic only -- no ffmpeg, no filesystem walk. A corpus of one kind of
material measures one kind of material, so selection is stratified by dialogue
density rather than taken as whatever the library returns first.
"""

from __future__ import annotations

from enum import Enum


class DensityBand(Enum):
    SPARSE = "sparse"
    NORMAL = "normal"
    DENSE = "dense"
    VERY_DENSE = "very_dense"


def band_for_cues_per_minute(cpm: float) -> DensityBand:
    """Bands are contiguous and cover the whole range, so nothing is unclassifiable."""
    if cpm < 8.0:
        return DensityBand.SPARSE
    if cpm < 18.0:
        return DensityBand.NORMAL
    if cpm < 30.0:
        return DensityBand.DENSE
    return DensityBand.VERY_DENSE


def stratify(items: list[tuple[str, float]], *, per_band: int) -> list[tuple[str, float]]:
    """Take up to ``per_band`` items from each populated band.

    Sorted before slicing so the same library yields the same corpus -- a study
    you cannot re-run against the same material is not reproducible.
    """
    buckets: dict[DensityBand, list[tuple[str, float]]] = {}
    for name, cpm in sorted(items):
        buckets.setdefault(band_for_cues_per_minute(cpm), []).append((name, cpm))
    out: list[tuple[str, float]] = []
    for band in DensityBand:
        out.extend(buckets.get(band, [])[:per_band])
    return out
```

- [ ] **Step 4: Run to verify it passes**

Expected: PASS, 4 passed

- [ ] **Step 5: Write the CLI**

`scripts/phase2_corpus.py` walks `/mnt/nas/Media`, reads any sibling `.srt` to compute
cues per minute via `subtitle_readability.parse_srt`, calls `stratify`, then extracts a
fixed-length clip from each pick with ffmpeg into `study/clips/`.

⚠️ **Verify the NAS with a real file read before walking it**, not `ls` — the
mountpoint dirs live on WSL's own disk and list content even when the share is gone.

⚠️ **Extract clips once and reuse them across all arms.** Re-extracting per arm would
let ffmpeg nondeterminism leak into a comparison that is supposed to isolate the
segmenter.

- [ ] **Step 6: Commit**

```bash
cd /c/Projects/subarr
git add src/subarr/study/corpus.py scripts/phase2_corpus.py tests/test_phase2_corpus.py
git commit -m "feat(#171): stratified corpus selection for the phase 2 study"
```

---

## Task 5: Noise calibration — run arm 3 twice

**This gates every comparison that follows.** Whisper is not bit-deterministic. Until
run-to-run variance in the primary metric is known, a 2pp difference between arms
cannot be told from noise.

- [ ] **Step 1:** Run arm 3 (`ghcr.io/coaxk/subarr-subgen:2026.07.3-r1`) over the full
  clip set. Save to `study/arm3-run1/`.
- [ ] **Step 2:** Run arm 3 again over the **identical** clips, same model, same compute
  type. Save to `study/arm3-run2/`.
- [ ] **Step 3:** Compute the post-retime 25-CPS share for both and report the delta.

**Decision rule, fixed in advance:**
- delta comfortably under 2pp (say ≤0.5pp) → corpus is adequate, proceed.
- delta approaching or exceeding 2pp → **the corpus is too small and the comparison is
  not yet meaningful.** Grow it and re-calibrate. Do **not** proceed and do not relax
  the gate to fit the noise.

⚠️ Report the measured delta whatever it is. A calibration that is quietly skipped
because it was inconvenient turns the entire study into an unfalsifiable claim.

---

## Task 6: Build arms 1 and 2, then run all three

Arm 3's image already exists. Arms 1 and 2 must be built **unpatched**:

- Arm 1: upstream `main` at the pinned 2026.07.3 commit
- Arm 2: `refactor/drop-stable-ts` at `7997624`

⚠️ **Neither gets our patch stack.** The point of arms 1 and 2 is to isolate the
segmenter with our tuning held out. Applying patches would defeat the design.

⚠️ **Arm 2 may not run at all without the `fw_kwargs` fix.** The branch passes
`word_timestamps`, `vad_filter`, `condition_on_previous_text`, `task` and `language`
as fixed keywords beside `**fw_kwargs`, so any of those arriving via `SUBGEN_KWARGS`
raises `TypeError` (McCloudS/subgen#355). Run arm 2 with a **clean `SUBGEN_KWARGS`**,
and if a local patch is needed to get it running, record it explicitly in the report —
a study run against a locally-modified arm 2 must say so.

Hold model, compute type, language and clip set identical across all three arms.
Record every one of those in the report; an unstated difference invalidates the
comparison.

---

## Task 7: Report and verdict

Generate `docs/171-phase2-quality-study.md` carrying:

- the noise calibration delta, and the corpus size it justified
- per-arm results, **pre-retime and post-retime**, for all three arms
- arm 1 vs 2 (segmenter alone), arm 3 vs 1 (**what strongpad actually buys** — call it
  out plainly if that delta is about zero)
- **arm 2 vs arm 3 post-retime: the decision**, with the gate verdict verbatim
- the semantic control, and an explicit statement of whether it moved
- every held-constant parameter, and any local modification made to get an arm running

Then post the outcome to subarr#171 and open the PR.

**The three outcomes, all legitimate:**
- gate passes → **adopt**; the migration becomes ordinary sync work on the 11 broken hunks
- gate fails on CPS → **do not adopt yet**; the retimer needs strengthening first
- hard fail on overlaps → **do not adopt**; report it upstream as a segmenter defect

---

## Self-review notes

- Every metric flows through `subtitle_readability` / `subtitle_retime`, so the study
  measures what the product measures.
- The gate, the tolerance, and the corpus-sizing rule are written down **above** any
  task that produces a number.
- The warn/critical CPS trap has a dedicated test and a watch-it-fail step.
- Aggregation pools cues rather than averaging per-file shares, with a test that
  fails if someone changes it.
