"""#359 off-app tuning: sweep RetimeParams across a subtitle corpus and report
pooled readability deltas. Pure + deterministic core; corpus adapters + CLI
elsewhere. The manual bootstrap of the federated-tuning loop (#124)."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, median

from .subtitle_readability import (
    CRITICAL_CPS,
    MAX_CPS,
    MAX_DURATION_S,
    MIN_DURATION_S,
    Cue,
    parse_srt,
)
from .subtitle_retime import RetimeParams, retime_srt


def param_grid() -> list[RetimeParams]:
    """The sweep grid: target_cps x min_cue_ms; gap/max held at Netflix values."""
    return [
        RetimeParams(target_cps=tc, min_cue_ms=mc, min_gap_ms=100, max_cue_ms=7000)
        for tc in (15.0, 17.0, 20.0)
        for mc in (833, 1000, 1200)
    ]


@dataclass(frozen=True)
class SweepRow:
    params: RetimeParams | None  # None = baseline (no re-timing)
    subs: int
    subs_changed: int
    median_cps: float
    pct_over_critical: float  # cues > CRITICAL_CPS (25)
    pct_over_comfortable: float  # cues > MAX_CPS (20)
    micro_cues: int  # cues < MIN_DURATION_S
    too_long: int  # cues > MAX_DURATION_S
    mean_added_ms: float  # mean screen-time added per sub


def _metrics(cues: list[Cue]) -> dict:
    live = [c for c in cues if c.duration_s > 0]
    cpses = [c.cps for c in live]
    n = len(cpses) or 1
    return {
        "median_cps": median(cpses) if cpses else 0.0,
        "pct_over_critical": sum(1 for x in cpses if x > CRITICAL_CPS) / n,
        "pct_over_comfortable": sum(1 for x in cpses if x > MAX_CPS) / n,
        "micro_cues": sum(1 for c in live if c.duration_s < MIN_DURATION_S),
        "too_long": sum(1 for c in live if c.duration_s > MAX_DURATION_S),
    }


def _dur_ms(cues: list[Cue]) -> int:
    return sum(c.end_ms - c.start_ms for c in cues)


def retime_sweep(texts: list[str], grid: list[RetimeParams]) -> list[SweepRow]:
    """Baseline row (params=None) first, then one row per grid combo. Metrics are
    pooled across all corpus cues; mean_added_ms is averaged per sub."""
    parsed = [parse_srt(t) for t in texts]
    before = [c for cues in parsed for c in cues]
    bm = _metrics(before)
    rows = [
        SweepRow(
            None,
            len(texts),
            0,
            bm["median_cps"],
            bm["pct_over_critical"],
            bm["pct_over_comfortable"],
            bm["micro_cues"],
            bm["too_long"],
            0.0,
        )
    ]
    for params in grid:
        after: list[Cue] = []
        added: list[int] = []
        changed = 0
        for text, b in zip(texts, parsed):
            new = retime_srt(text, params)
            a = parse_srt(new)
            after.extend(a)
            if new != text:
                changed += 1
            added.append(_dur_ms(a) - _dur_ms(b))
        m = _metrics(after)
        rows.append(
            SweepRow(
                params,
                len(texts),
                changed,
                m["median_cps"],
                m["pct_over_critical"],
                m["pct_over_comfortable"],
                m["micro_cues"],
                m["too_long"],
                mean(added) if added else 0.0,
            )
        )
    return rows
