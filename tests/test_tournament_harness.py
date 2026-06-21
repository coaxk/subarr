"""#65 Tier-B harness — judge REAL candidate SRTs for one source clip.

The reusable core: given candidate SRTs (however produced — subgen config
sweep, existing library subs, etc.) + the clip's VAD speech ranges, rank them
with the validated tournament judge and render a scorecard. This is what turns
"we have a validated judge" into "we can run a real comparison".
"""

from __future__ import annotations


def _h():
    from subarr import tournament_harness as h

    return h


CLEAN = (
    "1\n00:00:00,000 --> 00:00:02,000\nWhere are you going tonight?\n\n"
    "2\n00:00:02,000 --> 00:00:04,000\nI'm meeting a friend downtown.\n"
)
HALLUC = (
    "1\n00:00:00,000 --> 00:00:02,000\nThank you for watching.\n\n"
    "2\n00:00:02,000 --> 00:00:04,000\nThank you for watching.\n"
)


def test_judge_candidates_ranks_and_picks_winner():
    h = _h()
    result = h.judge_candidates(
        {"clean": CLEAN, "halluc": HALLUC},
        speech_ranges=[(0.0, 4.0)],
    )
    assert result.winner_label == "clean"
    assert [s.entrant_label for s in result.scorecards][0] == "clean"


def test_judge_candidates_honours_cps_thresholds():
    """#314: the per-sweep CPS bar threads judge_candidates → run_tournament →
    score_entrant → analyze_srt. A 30-CPS entrant carries a cps readability
    issue at the default bar and none when the sweep raises the bar past 30."""
    h = _h()
    fast = "1\n00:00:00,000 --> 00:00:01,000\n" + ("x" * 30) + "\n"  # 30 CPS
    default = h.judge_candidates({"fast": fast}, speech_ranges=[(0.0, 1.0)])
    assert any(i["kind"] == "cps" for i in default.scorecards[0].readability["issues"])
    relaxed = h.judge_candidates({"fast": fast}, speech_ranges=[(0.0, 1.0)], cps_max=35.0, cps_critical=40.0)
    assert not any(i["kind"] == "cps" for i in relaxed.scorecards[0].readability["issues"])


def test_judge_candidates_threads_gen_times_for_tiebreak():
    h = _h()
    # identical output → speed breaks the tie
    result = h.judge_candidates(
        {"slow": CLEAN, "fast": CLEAN},
        speech_ranges=[(0.0, 4.0)],
        gen_times={"slow": 100.0, "fast": 10.0},
    )
    assert result.winner_label == "fast"


def test_load_candidates_reads_srt_files(tmp_path):
    h = _h()
    (tmp_path / "default.srt").write_text(CLEAN, encoding="utf-8")
    (tmp_path / "noisy_robust.srt").write_text(HALLUC, encoding="utf-8")
    cands = h.load_candidates(tmp_path)
    assert set(cands.keys()) == {"default", "noisy_robust"}
    assert cands["default"] == CLEAN


def test_format_result_shows_winner_and_signals():
    h = _h()
    result = h.judge_candidates({"clean": CLEAN, "halluc": HALLUC}, speech_ranges=[(0.0, 4.0)])
    text = h.format_result(result)
    assert "clean" in text and "halluc" in text
    assert "WINNER" in text.upper()
    # the QE signals that decided it are visible
    assert "silence" in text.lower()
