"""#65 — tournament judging engine (the scoring/ranking heart).

Given several config "entrants" (each = a candidate SRT output for the SAME
source), score each objectively and rank them. Unblocked half of the
tournament: reuses the deterministic readability linter (#92); the live
per-variant generation (the "arena") is blocked on #88.

Rubric here is the v1 PROPOSAL (tunable) — these tests pin the *behaviour*
(clean beats sloppy, broken SRT is disqualified, faster wins a tie), not the
exact weights.
"""

from __future__ import annotations


def _t():
    from subarr import tournament as t

    return t


CLEAN = "1\n00:00:00,000 --> 00:00:03,000\nA calm, readable line.\n"
# Same content but flashed too fast (high CPS) — should score lower.
SLOPPY = "1\n00:00:00,000 --> 00:00:00,500\nA calm, readable line that flashes by far too fast to read.\n"
BROKEN = "not a subtitle file at all\njust noise\n"


# A silence clip = VAD ran and found NO speech (empty ranges). The ideal output
# is empty; any text is hallucination (the "thank you for watching" artifact).
SILENCE_CLIP: list = []
HALLUCINATION = "1\n00:00:01,000 --> 00:00:04,000\nThank you for watching until the end.\n"


def test_empty_output_wins_on_a_silence_clip():
    # Harness-proven: on 8s of silence, default hallucinated a YouTube outro
    # while noisy-robust (VAD) produced nothing. Empty is CORRECT here — it must
    # beat the hallucination, not be disqualified below it.
    t = _t()
    quiet = t.score_entrant(t.Entrant(label="quiet", srt_text="", speech_ranges=SILENCE_CLIP))
    halluc = t.score_entrant(t.Entrant(label="halluc", srt_text=HALLUCINATION, speech_ranges=SILENCE_CLIP))
    assert not quiet.disqualified
    assert quiet.composite >= 90.0  # correctly silent = clean pass
    assert quiet.composite > halluc.composite  # and it beats the hallucination


def test_hallucination_on_silence_is_penalised():
    t = _t()
    halluc = t.score_entrant(t.Entrant(label="halluc", srt_text=HALLUCINATION, speech_ranges=SILENCE_CLIP))
    assert halluc.composite < 50.0  # text on a known-silent clip is hallucination


def test_empty_still_disqualified_when_clip_has_speech():
    # Dropout: speech was present but nothing was transcribed → still DQ.
    t = _t()
    dropout = t.score_entrant(t.Entrant(label="dropout", srt_text="", speech_ranges=[(0.0, 10.0)]))
    assert dropout.disqualified and dropout.composite == 0.0


def test_empty_still_disqualified_when_no_vad_data():
    # ranges=None → can't tell silence from missing VAD → stay conservative (DQ).
    t = _t()
    e = t.score_entrant(t.Entrant(label="x", srt_text="", speech_ranges=None))
    assert e.disqualified


def test_clean_beats_sloppy():
    t = _t()
    result = t.run_tournament(
        [
            t.Entrant(label="sloppy", srt_text=SLOPPY),
            t.Entrant(label="clean", srt_text=CLEAN),
        ]
    )
    assert result.winner_label == "clean"
    # ranked best-first
    assert result.scorecards[0].entrant_label == "clean"
    assert result.scorecards[0].composite > result.scorecards[1].composite


def test_broken_srt_is_disqualified_and_last():
    t = _t()
    result = t.run_tournament(
        [
            t.Entrant(label="clean", srt_text=CLEAN),
            t.Entrant(label="broken", srt_text=BROKEN),
        ]
    )
    broken = next(s for s in result.scorecards if s.entrant_label == "broken")
    assert broken.disqualified is True
    assert broken.composite == 0
    assert result.scorecards[-1].entrant_label == "broken"
    assert result.winner_label == "clean"


def test_faster_entrant_wins_a_readability_tie():
    t = _t()
    # identical output → readability ties → speed is the tiebreak
    result = t.run_tournament(
        [
            t.Entrant(label="slow", srt_text=CLEAN, gen_time_s=120.0),
            t.Entrant(label="fast", srt_text=CLEAN, gen_time_s=20.0),
        ]
    )
    assert result.winner_label == "fast"


def test_scorecard_exposes_readability_and_composite():
    t = _t()
    result = t.run_tournament([t.Entrant(label="only", srt_text=CLEAN)])
    sc = result.scorecards[0]
    assert 0 <= sc.composite <= 100
    assert sc.readability is not None
    assert "clean" in sc.readability  # the #92 report dict, embedded
    assert sc.disqualified is False


def test_empty_tournament_is_safe():
    t = _t()
    result = t.run_tournament([])
    assert result.scorecards == []
    assert result.winner_label is None
