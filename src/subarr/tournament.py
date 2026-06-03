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
from .transcript_signals import (
    canned_phrase_hits,
    repeated_line_ratio,
    silence_text_ratio,
)

# --- v1 proposed rubric (TUNABLE) ---------------------------------------
CRITICAL_PENALTY = 2.0   # weight of a 'critical' readability issue
WARN_PENALTY = 1.0       # weight of a 'warn' readability issue
QUALITY_WEIGHT = 0.85    # quality (100 − QE − readability penalties) vs speed
SPEED_WEIGHT = 0.15

# Readability is the SECONDARY judge (#65 research: QE signals are the real
# discriminators; readability is advisory). It can shave at most
# READABILITY_CAP points off an otherwise-good transcript — never floor it.
# Tier-B 2026-06-04 showed the old "readability IS the base" rubric scored
# accurate, speech-aligned transcripts of rapid dialogue at ~0 (high CPS trips
# the linter), masking the QE signals that decide a tournament.
READABILITY_K = 25.0     # readability penalty = load_per_cue × K …
READABILITY_CAP = 20.0   # … capped here so readability stays secondary

# QE (reference-free) penalties — the real discriminators (#65 research). A
# quality score starts from readability (0-100) and these subtract from it, so
# a hallucinating/looping entrant tanks regardless of how "readable" its
# fabricated text is. Tuned so a fully-hallucinated or fully-looping output
# drops to ~0.
SILENCE_TEXT_PENALTY = 100.0   # × fraction of text over silence (hallucination)
REPEAT_PENALTY = 100.0         # × fraction of duplicate lines (looping)
CANNED_PENALTY = 40.0          # × canned-phrase cues (capped)
CANNED_CAP = 3


@dataclass
class Entrant:
    label: str                  # config name, e.g. "large-v3 / beam5 / vad"
    srt_text: str               # candidate output for the shared source
    gen_time_s: float | None = None
    config: dict[str, Any] = field(default_factory=dict)
    # silero speech ranges (s) for the SOURCE audio (#111). Shared across
    # entrants of one tournament (same source); enables the text-over-silence
    # hallucination judge. None → that judge is skipped (no penalty).
    speech_ranges: list[tuple[float, float]] | None = None


@dataclass
class Scorecard:
    entrant_label: str
    disqualified: bool
    composite: float            # 0-100 (0 if disqualified)
    readability_score: float    # 0-100
    cue_count: int
    gen_time_s: float | None
    readability: dict[str, Any] | None   # the #92 report.to_dict()
    signals: dict[str, Any] | None = None  # QE signals (silence/repeat/canned)
    notes: str = ""


@dataclass
class TournamentResult:
    scorecards: list[Scorecard]           # ranked best-first
    winner_label: str | None


def _readability_load(report: ReadabilityReport) -> float:
    """Issue load PER CUE so a config that produces more (or longer) cues
    isn't unfairly punished — entrants share a source but segment it
    differently, so absolute issue counts aren't comparable."""
    critical = sum(1 for i in report.issues if i.severity == "critical")
    warn = sum(1 for i in report.issues if i.severity == "warn")
    cues = max(report.cue_count, 1)
    return (critical * CRITICAL_PENALTY + warn * WARN_PENALTY) / cues


def _readability_score(report: ReadabilityReport) -> float:
    """0-100, for display on the scorecard. NOTE: this is informational only —
    the composite uses a CAPPED readability *penalty* (see score_entrant), not
    this score as its base. Kept so the UI can still show a readability grade."""
    return max(0.0, 100.0 - _readability_load(report) * 100.0)


def score_entrant(entrant: Entrant, fastest_time_s: float | None = None) -> Scorecard:
    cues = parse_srt(entrant.srt_text)
    if not cues:
        return Scorecard(
            entrant_label=entrant.label, disqualified=True, composite=0.0,
            readability_score=0.0, cue_count=0, gen_time_s=entrant.gen_time_s,
            readability=None, signals=None,
            notes="no parseable subtitle cues (disqualified)",
        )
    report = analyze_srt(entrant.srt_text)
    r_score = _readability_score(report)

    # QE-PRIMARY scoring (#65 research). Quality starts at 100 and the
    # reference-free QE signals — the real discriminators — subtract from it,
    # so a hallucinating/looping/canned entrant tanks regardless of how
    # "readable" its fabricated text is. Readability contributes only a CAPPED
    # secondary penalty, so an accurate transcript of fast dialogue (high CPS)
    # stays a high score instead of being floored.
    sil = silence_text_ratio(cues, entrant.speech_ranges)
    rep = repeated_line_ratio(cues)
    canned = canned_phrase_hits(cues)
    qe_penalty = (
        sil * SILENCE_TEXT_PENALTY
        + rep * REPEAT_PENALTY
        + min(canned, CANNED_CAP) * CANNED_PENALTY
    )
    readability_penalty = min(_readability_load(report) * READABILITY_K, READABILITY_CAP)
    quality = max(0.0, 100.0 - qe_penalty - readability_penalty)
    signals = {
        "silence_text_ratio": round(sil, 4),
        "repeated_line_ratio": round(rep, 4),
        "canned_phrase_hits": canned,
    }

    if entrant.gen_time_s and fastest_time_s and entrant.gen_time_s > 0:
        speed_score = min(100.0, (fastest_time_s / entrant.gen_time_s) * 100.0)
        composite = quality * QUALITY_WEIGHT + speed_score * SPEED_WEIGHT
    else:
        composite = quality
    return Scorecard(
        entrant_label=entrant.label, disqualified=False,
        composite=round(composite, 2), readability_score=round(r_score, 2),
        cue_count=report.cue_count, gen_time_s=entrant.gen_time_s,
        readability=report.to_dict(), signals=signals,
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
