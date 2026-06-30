"""#359: pure SRT re-timer — extend over-CPS cues into the gap before the next
cue (+ pad micro-cues), clamped to the gap and max duration; idempotent; never
shortens or creates overlaps. Base-agnostic (operates on final cue timestamps)."""

from __future__ import annotations

from subarr.subtitle_readability import Cue
from subarr.subtitle_retime import RetimeParams, retime_cues

P = RetimeParams()  # target_cps=17, min_cue_ms=1000, min_gap_ms=100, max_cue_ms=7000


def _cue(i, start_ms, end_ms, text="x"):
    return Cue(index=i, start_ms=start_ms, end_ms=end_ms, lines=text.split("\n"))


def test_hot_cue_extended_toward_target_when_gap_available():
    # 80 chars in 2.0s = 40 cps; next cue starts at 60s (huge gap).
    cues = [_cue(1, 0, 2000, "x" * 80), _cue(2, 60000, 61000, "next")]
    out = retime_cues(cues, P)
    # target 17 cps over 80 chars → ~4706ms duration; capped by max_cue_ms=7000.
    assert out[0].end_ms == 80 * 1000 // 17 or abs(out[0].end_ms - round(80 / 17 * 1000)) <= 1
    assert out[0].cps <= P.target_cps + 0.5
    assert out[0].start_ms == 0 and out[1].start_ms == 60000  # starts never move


def test_micro_cue_padded_to_min_duration():
    # 3 chars in 0.3s — comfortable CPS but a sub-1s micro-cue; gap is wide.
    cues = [_cue(1, 0, 300, "ok"), _cue(2, 30000, 31000, "next")]
    out = retime_cues(cues, P)
    assert out[0].end_ms == P.min_cue_ms  # padded to 1000ms


def test_no_gap_leaves_cue_unchanged_as_residual():
    # Hot cue but the next cue starts immediately (no room to extend).
    cues = [_cue(1, 0, 2000, "x" * 80), _cue(2, 2000, 4000, "next")]
    out = retime_cues(cues, P)
    assert out[0].end_ms == 2000  # untouched — Aftercare monitors the residual


def test_min_gap_always_preserved():
    cues = [_cue(1, 0, 2000, "x" * 80), _cue(2, 3000, 5000, "next")]
    out = retime_cues(cues, P)
    assert out[0].end_ms <= 3000 - P.min_gap_ms  # never within min_gap of next start


def test_never_exceeds_max_cue_ms():
    # Huge gap + very hot cue → extension capped at max_cue_ms.
    cues = [_cue(1, 0, 1000, "x" * 300), _cue(2, 60000, 61000, "next")]
    out = retime_cues(cues, P)
    assert out[0].end_ms == P.max_cue_ms  # 7000


def test_never_shortens_a_cue():
    # Already-long comfortable cue; nothing should pull its end in.
    cues = [_cue(1, 0, 5000, "x" * 10), _cue(2, 60000, 61000, "next")]
    out = retime_cues(cues, P)
    assert out[0].end_ms >= 5000


def test_last_cue_grows_up_to_max_cue_ms():
    cues = [_cue(1, 0, 1000, "x" * 80)]  # no successor
    out = retime_cues(cues, P)
    # target end ~4706 < max 7000, and no next-cue bound → take the target.
    assert abs(out[0].end_ms - round(80 / 17 * 1000)) <= 1


def test_overlapping_input_not_made_worse():
    # Input already overlaps (cue1 ends after cue2 starts). Don't extend further.
    cues = [_cue(1, 0, 5000, "x" * 80), _cue(2, 3000, 6000, "next")]
    out = retime_cues(cues, P)
    assert out[0].end_ms == 5000  # unchanged; no new/larger overlap


def test_idempotent():
    cues = [_cue(1, 0, 2000, "x" * 80), _cue(2, 60000, 61000, "n")]
    once = retime_cues(cues, P)
    twice = retime_cues(once, P)
    assert [(c.start_ms, c.end_ms) for c in once] == [(c.start_ms, c.end_ms) for c in twice]


def test_inputs_not_mutated():
    cues = [_cue(1, 0, 2000, "x" * 80), _cue(2, 60000, 61000, "n")]
    retime_cues(cues, P)
    assert cues[0].end_ms == 2000  # original untouched (pure)


def test_ms_to_ts_formats_correctly():
    from subarr.subtitle_retime import _ms_to_ts

    assert _ms_to_ts(0) == "00:00:00,000"
    assert _ms_to_ts(3_661_123) == "01:01:01,123"
    assert _ms_to_ts(-5) == "00:00:00,000"  # clamps negatives


def test_render_srt_roundtrips_through_parse():
    from subarr.subtitle_readability import parse_srt
    from subarr.subtitle_retime import render_srt

    cues = [_cue(1, 0, 2000, "line one\nline two"), _cue(2, 2500, 4000, "next")]
    text = render_srt(cues)
    back = parse_srt(text)
    assert [(c.start_ms, c.end_ms, c.lines) for c in back] == [
        (0, 2000, ["line one", "line two"]),
        (2500, 4000, ["next"]),
    ]


def test_render_srt_reindexes_1_based():
    cues = [_cue(7, 0, 1000, "a"), _cue(9, 2000, 3000, "b")]
    from subarr.subtitle_retime import render_srt

    text = render_srt(cues)
    assert text.split("\n")[0] == "1"
    assert "\n2\n" in "\n" + text  # second block re-indexed to 2
