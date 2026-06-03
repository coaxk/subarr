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
