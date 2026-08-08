"""Phase 2 primary metrics, extracted from an SRT.

Deliberately thin over subtitle_readability rather than a reimplementation --
the study must measure what the product measures, or it is measuring itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from subarr.subtitle_readability import analyze_srt, parse_srt
from subarr.subtitle_retime import retime_srt


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

    ``over_25_cps`` counts ONLY ``severity == "critical"`` cps issues.
    ``analyze_cues`` raises a *warn* cps issue above MAX_CPS (20.0) and a
    *critical* one above CRITICAL_CPS (25.0), and ``ReadabilityReport.counts``
    sums both. The gate is defined on 25 CPS, so counting the warns would
    measure the wrong threshold and apply the +2pp tolerance to a number two to
    three times too large.
    """
    report = analyze_srt(text)
    return SrtMetrics(
        cue_count=len(parse_srt(text)),
        over_25_cps=sum(1 for i in report.issues if i.kind == "cps" and i.severity == "critical"),
        overlaps=sum(1 for i in report.issues if i.kind == "overlap"),
    )


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
    # round() only: without it, e.g. 7/100*100 - 5/100*100 == 2.000000000000001,
    # not exactly 2.0, so an exact-tolerance boundary would fail on float noise
    # alone. 9 dp is far below the 1 dp shown in the reason string.
    delta = round(cand_pp - ref_pp, 9)
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
