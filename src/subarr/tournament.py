"""#65 — tournament judging engine.

The scoring/ranking HEART of the Whisper-tuning tournament. Given several
config "entrants" (each a candidate SRT output for the SAME source audio), it
judges each objectively and ranks them so the winner can be adopted.

This is the UNBLOCKED half. The "arena" — running the same audio through each
config variant live via subgen — is blocked on #88 (per-request kwargs; today
SUBGEN_KWARGS is global-only). The judges here operate on already-produced
outputs, so they're fully buildable + testable now.

Judges (composable):
  - readability (#92, deterministic): CPS/CPL/timing/overlap.
  - SRT integrity: did it parse to real cues at all? A config that produces
    garbage / no cues is DISQUALIFIED, not merely low-scored.
  - reference-free QE (#94): HOOK only — wire when the QE experiment lands.
  - speed: wall-clock, used as a tiebreak.

The RUBRIC (weights/penalties below) is the v1 PROPOSAL — tune with Judd. The
*shape* is intentional and what the tests pin: integrity is a gate,
readability dominates, speed breaks ties, QE slots in as an advisory
contributor later.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .subtitle_readability import ReadabilityReport, analyze_srt, parse_srt

# --- v1 proposed rubric (TUNABLE) ---------------------------------------
CRITICAL_PENALTY = 2.0   # weight of a 'critical' readability issue
WARN_PENALTY = 1.0       # weight of a 'warn' readability issue
READABILITY_WEIGHT = 0.85
SPEED_WEIGHT = 0.15
# QE_WEIGHT reserved for #94; renormalise READABILITY/SPEED when it's added.


@dataclass
class Entrant:
    label: str                  # config name, e.g. "large-v3 / beam5 / vad"
    srt_text: str               # candidate output for the shared source
    gen_time_s: float | None = None
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class Scorecard:
    entrant_label: str
    disqualified: bool
    composite: float            # 0-100 (0 if disqualified)
    readability_score: float    # 0-100
    cue_count: int
    gen_time_s: float | None
    readability: dict[str, Any] | None   # the #92 report.to_dict()
    notes: str = ""


@dataclass
class TournamentResult:
    scorecards: list[Scorecard]           # ranked best-first
    winner_label: str | None


def _readability_score(report: ReadabilityReport) -> float:
    """0-100. Penalise issues PER CUE so a config that produces more (or
    longer) cues isn't unfairly punished — entrants share a source but
    segment it differently, so absolute issue counts aren't comparable."""
    critical = sum(1 for i in report.issues if i.severity == "critical")
    warn = sum(1 for i in report.issues if i.severity == "warn")
    cues = max(report.cue_count, 1)
    load_per_cue = (critical * CRITICAL_PENALTY + warn * WARN_PENALTY) / cues
    return max(0.0, 100.0 - load_per_cue * 100.0)


def score_entrant(entrant: Entrant, fastest_time_s: float | None = None) -> Scorecard:
    if not parse_srt(entrant.srt_text):
        return Scorecard(
            entrant_label=entrant.label, disqualified=True, composite=0.0,
            readability_score=0.0, cue_count=0, gen_time_s=entrant.gen_time_s,
            readability=None, notes="no parseable subtitle cues (disqualified)",
        )
    report = analyze_srt(entrant.srt_text)
    r_score = _readability_score(report)
    if entrant.gen_time_s and fastest_time_s and entrant.gen_time_s > 0:
        speed_score = min(100.0, (fastest_time_s / entrant.gen_time_s) * 100.0)
        composite = r_score * READABILITY_WEIGHT + speed_score * SPEED_WEIGHT
    else:
        composite = r_score
    return Scorecard(
        entrant_label=entrant.label, disqualified=False,
        composite=round(composite, 2), readability_score=round(r_score, 2),
        cue_count=report.cue_count, gen_time_s=entrant.gen_time_s,
        readability=report.to_dict(),
    )


def run_tournament(entrants: list[Entrant]) -> TournamentResult:
    if not entrants:
        return TournamentResult(scorecards=[], winner_label=None)
    times = [e.gen_time_s for e in entrants if e.gen_time_s]
    fastest = min(times) if times else None
    cards = [score_entrant(e, fastest_time_s=fastest) for e in entrants]
    # Disqualified always last; otherwise composite desc, faster breaks ties.
    cards.sort(key=lambda c: (
        c.disqualified,
        -c.composite,
        c.gen_time_s if c.gen_time_s is not None else float("inf"),
    ))
    winner = next((c.entrant_label for c in cards if not c.disqualified), None)
    return TournamentResult(scorecards=cards, winner_label=winner)
