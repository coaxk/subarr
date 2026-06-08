"""#156 aftercare evaluation + flag bar (pure)."""
from __future__ import annotations

from subarr.aftercare import AftercareEvaluation, evaluate_subtitle

_CLEAN = (
    "1\n00:00:01,000 --> 00:00:03,000\nHello there.\n\n"
    "2\n00:00:04,000 --> 00:00:06,500\nHow are you today?\n\n"
)
_LOOPING = "".join(
    f"{i}\n00:00:{i:02d},000 --> 00:00:{i:02d},800\nThanks for watching!\n\n"
    for i in range(1, 11)
)


def test_clean_subtitle_not_flagged():
    ev = evaluate_subtitle(_CLEAN)
    assert isinstance(ev, AftercareEvaluation)
    assert ev.flagged is False
    assert ev.cue_count == 2
    assert ev.composite > 65


def test_canned_hallucination_flagged():
    ev = evaluate_subtitle(_LOOPING)   # repeats + canned "Thanks for watching!"
    assert ev.flagged is True
    assert (ev.signals or {}).get("canned_phrase_hits", 0) > 0


def test_empty_subtitle_flagged_and_disqualified():
    ev = evaluate_subtitle("")
    assert ev.flagged is True
    assert ev.cue_count == 0
