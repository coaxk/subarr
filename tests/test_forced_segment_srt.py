"""#364 slice 1 — span+text cues -> a forced .forced.en.srt string. Absolute
timing, 1..N re-indexed, multi-line preserved."""

from __future__ import annotations

from subarr.forced_segment import build_forced_srt


def test_builds_absolute_timed_reindexed_srt():
    cues = [(60000, 63000, "Hello there"), (600000, 604000, "Line one\nLine two")]
    srt = build_forced_srt(cues)
    assert srt == (
        "1\n00:01:00,000 --> 00:01:03,000\nHello there\n\n"
        "2\n00:10:00,000 --> 00:10:04,000\nLine one\nLine two\n"
    )


def test_empty_cues_render_empty_string():
    assert build_forced_srt([]) == ""


def test_blank_and_whitespace_text_lines_are_dropped():
    srt = build_forced_srt([(0, 2000, "  keep  \n\n  ")])
    assert srt == "1\n00:00:00,000 --> 00:00:02,000\nkeep\n"
