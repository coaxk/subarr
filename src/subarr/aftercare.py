"""#156 Track A: judge a completed job's subtitle with subarr's own judges.

Pure wrapper over `tournament.score_entrant` (readability #92 + failure-mode
signals). NO accuracy/QE here (that's L3/#123) and NO VAD, so silence/uncovered
signals are inert — the trustworthy detectors are readability, looping (repeats)
and canned-hallucination. The composite is a failure-absence + readability
rollup, NOT a quality grade; callers must present it as such.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .tournament import Entrant, score_entrant

# Flag-bar thresholds (tunable; exposed as constants so Settings can surface
# them later without touching logic).
AFTERCARE_COMPOSITE_MIN = 65.0
AFTERCARE_REPEAT_MAX = 0.20


@dataclass
class AftercareEvaluation:
    composite: float
    cue_count: int
    flagged: bool
    readability: dict[str, Any] | None  # ReadabilityReport.to_dict() or None
    signals: dict[str, Any] | None  # score_entrant signals or None


def _is_flagged(card) -> bool:
    """A job is flagged when any Track-A-available signal trips. (silence_text /
    uncovered need VAD -> inert here; qe_adequacy needs source -> inert here.)"""
    if card.disqualified:
        return True
    sig = card.signals or {}
    if (sig.get("canned_phrase_hits") or 0) > 0:
        return True
    if (sig.get("repeated_line_ratio") or 0.0) > AFTERCARE_REPEAT_MAX:
        return True
    issues = (card.readability or {}).get("issues", [])
    if any(i.get("severity") == "critical" for i in issues):
        return True
    if (card.composite or 0.0) < AFTERCARE_COMPOSITE_MIN:
        return True
    return False


def evaluate_subtitle(srt_text: str) -> AftercareEvaluation:
    """Judge one produced subtitle. No source_text / speech_ranges (Track A)."""
    card = score_entrant(Entrant(label="aftercare", srt_text=srt_text))
    return AftercareEvaluation(
        composite=float(card.composite),
        cue_count=int(card.cue_count),
        flagged=_is_flagged(card),
        readability=card.readability,
        signals=card.signals,
    )
