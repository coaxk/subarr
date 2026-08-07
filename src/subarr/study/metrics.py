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
