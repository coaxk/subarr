from subarr.study.metrics import (
    ArmResult,
    ArmSample,
    GateVerdict,
    SrtMetrics,
    aggregate,
    evaluate_gate,
    metrics_for_srt,
    sample_for_srt,
)

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


def test_sample_carries_both_stages_and_they_can_differ():
    srt = """1
00:00:00,000 --> 00:00:02,000
{sixty}

2
00:00:20,000 --> 00:00:22,000
short
""".replace("{sixty}", "x" * 60)
    s = sample_for_srt("clip01", srt)
    assert isinstance(s, ArmSample)
    assert s.clip == "clip01"
    assert s.pre.over_25_cps == 1
    assert s.post.cue_count == s.pre.cue_count


def test_post_stage_is_the_retimed_text_not_the_original():
    srt = """1
00:00:00,000 --> 00:00:02,000
{sixty}
""".replace("{sixty}", "x" * 60)
    s = sample_for_srt("c", srt)
    assert s.post_srt != s.pre_srt


def _sample(clip, cue_count, over, overlaps):
    m = SrtMetrics(cue_count=cue_count, over_25_cps=over, overlaps=overlaps)
    return ArmSample(clip=clip, pre_srt="", post_srt="", pre=m, post=m)


def test_aggregate_pools_cues_rather_than_averaging_per_file_shares():
    # A 1-cue file and a 99-cue file must not carry equal weight -- averaging
    # per-file shares would let one short clip dominate the primary metric.
    r = aggregate("arm2", [_sample("a", 1, 1, 0), _sample("b", 99, 0, 0)])
    assert isinstance(r, ArmResult)
    assert r.cue_count == 100
    assert r.over_25_cps_share == 0.01


def test_gate_passes_when_within_two_points_and_no_new_overlaps():
    ref = aggregate("arm3", [_sample("a", 100, 5, 0)])
    cand = aggregate("arm2", [_sample("a", 100, 7, 0)])
    assert evaluate_gate(candidate=cand, reference=ref).passed is True


def test_gate_fails_when_cps_share_exceeds_the_two_point_tolerance():
    ref = aggregate("arm3", [_sample("a", 100, 5, 0)])
    cand = aggregate("arm2", [_sample("a", 100, 8, 0)])
    assert evaluate_gate(candidate=cand, reference=ref).passed is False


def test_any_new_overlap_is_a_hard_fail_even_with_better_cps():
    # Strictly better CPS, one new overlap. The retimer guarantees zero new
    # overlaps; a segmenter that produces them is disqualified outright.
    ref = aggregate("arm3", [_sample("a", 100, 20, 0)])
    cand = aggregate("arm2", [_sample("a", 100, 1, 1)])
    v = evaluate_gate(candidate=cand, reference=ref)
    assert v.passed is False
    assert "overlap" in v.reason.lower()


def test_verdict_states_the_measured_numbers_not_just_a_boolean():
    ref = aggregate("arm3", [_sample("a", 100, 5, 0)])
    cand = aggregate("arm2", [_sample("a", 100, 7, 0)])
    v = evaluate_gate(candidate=cand, reference=ref)
    assert isinstance(v, GateVerdict)
    assert "5.0" in v.reason and "7.0" in v.reason
