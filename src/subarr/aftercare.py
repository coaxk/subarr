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

from .subtitle_readability import parse_srt
from .tournament import Entrant, score_entrant

# Flag-bar thresholds (tunable; exposed as constants so Settings can surface
# them later without touching logic).
AFTERCARE_COMPOSITE_MIN = 65.0
AFTERCARE_REPEAT_MAX = 0.20
# #216: cues running this far past the media's actual end = desynced or
# generated against the wrong cut. Generous on purpose — container duration
# and the last cue legitimately disagree by a few seconds.
AFTERCARE_SYNC_OVERRUN_MAX_S = 30.0


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
    # #216: ad/boilerplate in a GENERATED sub is hallucination; sync overrun
    # means the cues outlive the media. Both come in via the signals dict
    # (ad hits from score_entrant, overrun added by evaluate_subtitle).
    if (sig.get("ad_boilerplate_hits") or 0) > 0:
        return True
    if (sig.get("sync_overrun_s") or 0.0) > AFTERCARE_SYNC_OVERRUN_MAX_S:
        return True
    if (sig.get("repeated_line_ratio") or 0.0) > AFTERCARE_REPEAT_MAX:
        return True
    issues = (card.readability or {}).get("issues", [])
    if any(i.get("severity") == "critical" for i in issues):
        return True
    if (card.composite or 0.0) < AFTERCARE_COMPOSITE_MIN:
        return True
    return False


def evaluate_subtitle(srt_text: str, *, media_duration_s: float | None = None) -> AftercareEvaluation:
    """Judge one produced subtitle. No source_text / speech_ranges (Track A).

    media_duration_s (optional, from the ffprobe cache) enables the #216
    sync-overrun check: cues ending well past the media's end mean the sub
    was generated against a different cut or has drifted. Span coverage is
    recorded as a signal but never flags (sparse end-of-file dialogue is
    legitimate)."""
    card = score_entrant(Entrant(label="aftercare", srt_text=srt_text))
    signals = dict(card.signals or {})
    if media_duration_s and media_duration_s > 0:
        try:
            cues = parse_srt(srt_text)
            if cues:
                last_end_s = max(c.end_ms for c in cues) / 1000.0
                signals["sync_overrun_s"] = round(max(0.0, last_end_s - media_duration_s), 1)
                signals["sync_span_ratio"] = round(last_end_s / media_duration_s, 3)
        except Exception:  # noqa: BLE001 - a sync hint must never break judging
            pass
    card.signals = signals or None
    return AftercareEvaluation(
        composite=float(card.composite),
        cue_count=int(card.cue_count),
        flagged=_is_flagged(card),
        readability=card.readability,
        signals=card.signals,
    )
