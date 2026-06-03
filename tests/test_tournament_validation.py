"""#65 — TOURNAMENT VALIDATION (Tier A, synthetic).

Does the judging methodology actually rank a good transcript above the
failure modes Whisper configs produce? We feed a clean, speech-aligned
transcript against deliberately-degraded variants (text over silence,
looping, canned hallucinations) and assert the clean one wins.

This validates that the JUDGES detect what they're supposed to — the
prerequisite for trusting a real (Tier B) tournament verdict. No subgen, no
ground truth: synthetic speech_ranges stand in for the VAD pass.
"""
from __future__ import annotations


def _t():
    from subarr import tournament as t
    return t


# Clean, speech-aligned dialogue across the whole 6s clip.
CLEAN = (
    "1\n00:00:00,000 --> 00:00:02,000\nWhere are you going tonight?\n\n"
    "2\n00:00:02,000 --> 00:00:04,000\nI'm meeting a friend downtown.\n\n"
    "3\n00:00:04,000 --> 00:00:06,000\nWe'll be back before midnight.\n"
)
SPEECH_FULL = [(0.0, 6.0)]


def test_clean_beats_hallucination_over_silence():
    t = _t()
    # Canned phrases stamped over a clip that's almost entirely silent
    # (speech only 0–0.5s) — the classic non-speech hallucination.
    halluc = (
        "1\n00:00:00,000 --> 00:00:02,000\nThank you for watching.\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\nThank you for watching.\n\n"
        "3\n00:00:04,000 --> 00:00:06,000\nSubtitles by amara.org\n"
    )
    res = t.run_tournament([
        t.Entrant(label="halluc", srt_text=halluc, speech_ranges=[(0.0, 0.5)]),
        t.Entrant(label="clean", srt_text=CLEAN, speech_ranges=SPEECH_FULL),
    ])
    assert res.winner_label == "clean"
    halluc_card = next(s for s in res.scorecards if s.entrant_label == "halluc")
    clean_card = next(s for s in res.scorecards if s.entrant_label == "clean")
    assert clean_card.composite > halluc_card.composite


def test_clean_beats_looping_decoder():
    t = _t()
    loop = ""
    for i in range(1, 7):
        loop += f"{i}\n00:00:0{i-1},000 --> 00:00:0{i},000\nyou\n\n"
    res = t.run_tournament([
        t.Entrant(label="loop", srt_text=loop, speech_ranges=SPEECH_FULL),
        t.Entrant(label="clean", srt_text=CLEAN, speech_ranges=SPEECH_FULL),
    ])
    assert res.winner_label == "clean"


def test_signals_surface_on_scorecard():
    t = _t()
    res = t.run_tournament([t.Entrant(label="only", srt_text=CLEAN, speech_ranges=SPEECH_FULL)])
    sc = res.scorecards[0]
    # the QE signals are exposed for inspection / the future arena UI
    assert sc.signals is not None
    assert "silence_text_ratio" in sc.signals
    assert "repeated_line_ratio" in sc.signals
    assert "canned_phrase_hits" in sc.signals


# Real, accurate, speech-aligned transcript of RAPID film dialogue: short cue
# durations push CPS over the readability ceiling, but the text is correct and
# the QE discriminators are all clean. (Lifted from the Tier-B 2026-06-04 run:
# Air (2023) @1000s — every preset produced exactly this and the judge scored
# it 0.00, masking the QE signals.)
FAST_CLEAN = (
    "1\n00:00:00,000 --> 00:00:00,740\nYou know what I do here.\n\n"
    "2\n00:00:01,900 --> 00:00:03,040\nYou're losing, Sonny.\n\n"
    "3\n00:00:03,060 --> 00:00:05,560\nJust because you lose doesn't mean it wasn't a good bet.\n\n"
    "4\n00:00:05,940 --> 00:00:07,480\nThat perfect result is nonsense.\n\n"
    "5\n00:00:07,720 --> 00:00:08,350\nIt is not nonsense.\n"
)
FAST_RANGES = [(0.0, 8.5)]


def test_accurate_fast_dialogue_is_not_floored_by_readability():
    """Tier-B bug (2026-06-04): an accurate, speech-aligned transcript of fast
    dialogue scored ~0 purely because high CPS tripped the readability linter.
    Readability is the SECONDARY judge (#65 research) — a transcript with no
    hallucination, looping, or canned text must score as a GOOD result even
    when its cues flash fast. It must not be floored."""
    t = _t()
    sc = t.run_tournament([
        t.Entrant(label="fast_clean", srt_text=FAST_CLEAN, speech_ranges=FAST_RANGES),
    ]).scorecards[0]
    assert not sc.disqualified
    # QE signals are clean — this is genuinely good output.
    assert sc.signals["repeated_line_ratio"] == 0.0
    assert sc.signals["canned_phrase_hits"] == 0
    assert sc.signals["silence_text_ratio"] < 0.2
    # ...so it must NOT be floored by readability alone.
    assert sc.composite > 50, f"good fast-dialogue floored to {sc.composite}"


def test_readability_is_secondary_to_qe():
    """A QE-clean transcript with POOR readability (fast CPS) must outrank a
    perfectly-readable transcript that is actually a hallucination over
    silence. Pins the re-architecture: QE drives the score, readability is a
    capped secondary penalty — not the dominant base that can floor everything."""
    t = _t()
    # well-timed, low-CPS cues (great readability) but canned text stamped over
    # a clip that is almost entirely silent — the classic hallucination.
    readable_halluc = (
        "1\n00:00:00,000 --> 00:00:02,000\nThank you for watching.\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\nPlease subscribe to the channel.\n\n"
        "3\n00:00:04,000 --> 00:00:06,000\nSubtitles by amara.org\n"
    )
    res = t.run_tournament([
        # readable hallucination listed FIRST so a naive tie resolves to it.
        t.Entrant(label="readable_halluc", srt_text=readable_halluc, speech_ranges=[(0.0, 0.5)]),
        t.Entrant(label="fast_clean", srt_text=FAST_CLEAN, speech_ranges=FAST_RANGES),
    ])
    assert res.winner_label == "fast_clean"
