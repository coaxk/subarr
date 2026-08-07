from subarr.study.metrics import SrtMetrics, metrics_for_srt

# Cue 1: 10 chars in 10s  -> 1 CPS, fine.
# Cue 2: 60 chars in 2s   -> 30 CPS, ABOVE the 25 critical threshold.
# Cue 3: 44 chars in 2s   -> 22 CPS, above the 20 WARN threshold but BELOW 25.
SRT = """1
00:00:00,000 --> 00:00:10,000
ten chars.

2
00:00:10,000 --> 00:00:12,000
{sixty}

3
00:00:12,000 --> 00:00:14,000
{fortyfour}
""".replace("{sixty}", "x" * 60).replace("{fortyfour}", "y" * 44)


def test_only_cues_over_25_cps_count_toward_the_primary_metric():
    # The 22 CPS cue is a "warn" in ReadabilityReport.counts["cps"] and must NOT
    # be counted. Counting it would measure the 20 CPS threshold instead and
    # inflate the primary metric the gate is defined on.
    m = metrics_for_srt(SRT)
    assert m.cue_count == 3
    assert m.over_25_cps == 1
    assert m.over_25_cps_share == 1 / 3


def test_overlap_count_is_reported_separately():
    overlapping = """1
00:00:00,000 --> 00:00:05,000
first

2
00:00:03,000 --> 00:00:06,000
second
"""
    assert metrics_for_srt(overlapping).overlaps == 1


def test_empty_srt_yields_zero_share_and_does_not_divide_by_zero():
    m = metrics_for_srt("")
    assert m.cue_count == 0
    assert m.over_25_cps_share == 0.0


def test_metrics_are_a_plain_comparable_record():
    a, b = metrics_for_srt(SRT), metrics_for_srt(SRT)
    assert a == b
    assert isinstance(a, SrtMetrics)
