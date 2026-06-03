"""#92 — deterministic subtitle readability linter.

Pure SRT math against Netflix/BBC-style norms (CPS / CPL / line count /
duration / overlap). No model, no GPU, no network. These tests pin the
parser and the thresholds.
"""
from __future__ import annotations


def _mod():
    from subarr import subtitle_readability as sr
    return sr


SAMPLE = """1
00:00:01,000 --> 00:00:03,000
Hello there.

2
00:00:04,000 --> 00:00:05,000
This is fine.
"""


def test_parse_srt_basic():
    sr = _mod()
    cues = sr.parse_srt(SAMPLE)
    assert len(cues) == 2
    assert cues[0].index == 1
    assert cues[0].start_ms == 1000
    assert cues[0].end_ms == 3000
    assert cues[0].lines == ["Hello there."]
    assert cues[0].duration_s == 2.0


def test_clean_subtitle_has_no_issues():
    sr = _mod()
    # ~22 chars over 3s = ~7 CPS, 1 line, comfortable duration
    srt = "1\n00:00:00,000 --> 00:00:03,000\nA calm, readable line.\n"
    report = sr.analyze_srt(srt)
    assert report.issues == []
    assert report.cue_count == 1
    assert report.clean is True


def test_cps_flagged_when_too_fast():
    sr = _mod()
    srt = "1\n00:00:00,000 --> 00:00:01,000\n" + ("x" * 80) + "\n"  # 80 CPS
    report = sr.analyze_srt(srt)
    assert any(i.kind == "cps" for i in report.issues)
    assert report.clean is False


def test_cpl_flagged_when_line_too_long():
    sr = _mod()
    srt = f"1\n00:00:00,000 --> 00:00:05,000\n{'y' * 60}\n"  # 60 > 42 CPL
    report = sr.analyze_srt(srt)
    assert any(i.kind == "cpl" for i in report.issues)


def test_too_many_lines_flagged():
    sr = _mod()
    srt = "1\n00:00:00,000 --> 00:00:05,000\nline one\nline two\nline three\n"
    report = sr.analyze_srt(srt)
    assert any(i.kind == "lines" for i in report.issues)


def test_overlap_flagged():
    sr = _mod()
    srt = (
        "1\n00:00:00,000 --> 00:00:03,000\nfirst\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\nsecond\n"
    )
    report = sr.analyze_srt(srt)
    assert any(i.kind == "overlap" for i in report.issues)


def test_too_short_and_too_long_duration():
    sr = _mod()
    rep_s = sr.analyze_srt("1\n00:00:00,000 --> 00:00:00,300\nflash\n")
    assert any(i.kind == "too_short" for i in rep_s.issues)
    rep_l = sr.analyze_srt("1\n00:00:00,000 --> 00:00:09,000\nlingers\n")
    assert any(i.kind == "too_long" for i in rep_l.issues)


def test_report_counts_and_to_dict():
    sr = _mod()
    report = sr.analyze_srt("1\n00:00:00,000 --> 00:00:01,000\n" + ("z" * 90) + "\n")
    assert report.counts.get("cps", 0) >= 1
    d = report.to_dict()
    assert d["clean"] is False
    assert d["cue_count"] == 1
    assert "cps" in d["counts"]
