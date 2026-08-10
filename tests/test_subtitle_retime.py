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
    # target 17 cps over 80 chars → ~4706ms duration (well under the 7000ms cap).
    assert abs(out[0].end_ms - round(80 / 17 * 1000)) <= 1
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
    # Compare against the next cue's ACTUAL start, not the input literal: #445
    # borrowing may legitimately have moved it later, and hard-coding 3000 here
    # would fail on a gap that is still perfectly preserved.
    assert out[0].end_ms <= out[1].start_ms - P.min_gap_ms


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


# A translate-mode-style fixture: several cramped, high-CPS cues with real gaps
# between them (translated text overruns the speech window).
_HOT_SRT = """1
00:00:00,000 --> 00:00:02,000
This is a very long translated line that crams far too many characters

2
00:00:05,000 --> 00:00:06,200
Another dense cue with way more text than fits comfortably here now

3
00:00:20,000 --> 00:00:20,300
hi
"""

# A comfortable transcribe-mode fixture: low CPS, adequate durations.
_CALM_SRT = """1
00:00:00,000 --> 00:00:03,000
Hello there.

2
00:00:04,000 --> 00:00:07,000
General Kenobi.
"""


def test_retime_srt_reduces_cps_and_zeroes_micro_cues_no_new_overlap():
    from subarr.subtitle_readability import CRITICAL_CPS, analyze_srt, parse_srt
    from subarr.subtitle_retime import retime_srt

    before = parse_srt(_HOT_SRT)
    after = parse_srt(retime_srt(_HOT_SRT))

    def median_cps(cues):
        vals = sorted(c.cps for c in cues if c.duration_s > 0)
        return vals[len(vals) // 2]

    def pct_over_critical(cues):
        return sum(1 for c in cues if c.cps > CRITICAL_CPS) / len(cues)

    def micro_count(cues):
        return sum(1 for c in cues if 0 < c.duration_s < 0.833)

    assert median_cps(after) < median_cps(before)
    assert pct_over_critical(after) <= pct_over_critical(before)
    assert micro_count(after) == 0
    # no NEW overlaps introduced
    rep = analyze_srt(retime_srt(_HOT_SRT))
    assert rep.counts.get("overlap", 0) == 0


def test_retime_srt_leaves_comfortable_input_essentially_unchanged():
    from subarr.subtitle_readability import parse_srt
    from subarr.subtitle_retime import retime_srt

    before = parse_srt(_CALM_SRT)
    after = parse_srt(retime_srt(_CALM_SRT))
    # already comfortable + adequate duration → no end moved.
    assert [c.end_ms for c in before] == [c.end_ms for c in after]


# ── #445: borrow slack from the next cue ───────────────────────────────────
#
# The end-extension above cannot help a cue whose neighbour starts immediately.
# Measured on the #171 Phase 2 corpus, 120 of 120 surviving over-CPS cues on the
# new-segmenter arm were blocked by exactly that: no gap to grow into.
#
# Borrowing pushes the NEXT cue's start LATER to lend time, only as far as that
# cue can spare while staying under the 25 CPS bar itself. It never moves a
# start earlier, so text is never displayed before its speech.


def test_borrow_lends_time_from_a_slack_neighbour():
    # A: 60 chars in 2.0s = 30 cps, no gap. Reaching the 25 cps bar needs
    # 2400ms, i.e. 400ms of borrow -- inside the 500ms cap. B has 4 chars in
    # 6.0s, so ample slack.
    cues = [_cue(1, 0, 2000, "x" * 60), _cue(2, 2000, 8000, "next")]
    out = retime_cues(cues, RetimeParams(max_borrow_ms=500))
    assert out[0].end_ms == 2400, "A extended exactly to the 25 cps bar"
    assert out[1].start_ms == 2400, "B's start moves later by the same amount"
    assert out[1].end_ms == 8000, "B's end never moves"
    assert out[0].cps <= 25.0 + 0.01


def test_borrow_never_moves_a_start_earlier():
    # The whole point: no cue may ever be displayed before its speech.
    cues = [_cue(1, 0, 2000, "x" * 80), _cue(2, 2000, 8000, "next")]
    out = retime_cues(cues, RetimeParams(max_borrow_ms=500))
    for before, after in zip(cues, out):
        assert after.start_ms >= before.start_ms


def test_borrow_never_pushes_the_neighbour_over_the_cps_bar():
    # Assert the INVARIANT, not a hand-computed number. B is itself extended by
    # the target_cps pass before any borrowing happens, which changes how much
    # slack it has -- a literal here encodes a mental model rather than the
    # contract, and the first draft of this test got that number wrong.
    cues = [_cue(1, 0, 2000, "x" * 80), _cue(2, 2000, 7000, "y" * 100)]
    out = retime_cues(cues, RetimeParams(max_borrow_ms=10_000))
    assert out[1].cps <= 25.0 + 0.01, "lending must never push B over the bar"
    assert out[0].end_ms > 2000, "A gained time"
    assert out[1].start_ms >= 2000, "B never starts earlier"


def test_borrow_beyond_the_cap_is_refused_entirely():
    # A needs 1200ms to reach the bar (80 chars). With a 300ms cap the job
    # cannot be finished, so NOTHING is borrowed -- the viewer is not charged a
    # late cue for a subtitle that stays unreadable anyway. This is also what
    # keeps the pass idempotent under a binding cap.
    cues = [_cue(1, 0, 2000, "x" * 80), _cue(2, 2000, 20000, "next")]
    out = retime_cues(cues, RetimeParams(max_borrow_ms=300))
    assert out[0].end_ms == 2000, "no partial borrow"
    assert out[1].start_ms == 2000, "B untouched"


def test_borrow_disabled_at_zero_leaves_timings_untouched():
    # Off switch, and the back-compat guarantee for anything pinned to the
    # pre-#445 behaviour.
    cues = [_cue(1, 0, 2000, "x" * 80), _cue(2, 2000, 8000, "next")]
    assert retime_cues(cues, RetimeParams(max_borrow_ms=0)) == retime_cues(
        cues, RetimeParams(max_borrow_ms=0)
    )
    out = retime_cues(cues, RetimeParams(max_borrow_ms=0))
    assert out[0].end_ms == 2000 and out[1].start_ms == 2000


def test_borrow_creates_no_overlaps():
    cues = [
        _cue(1, 0, 2000, "x" * 80),
        _cue(2, 2000, 8000, "y" * 20),
        _cue(3, 8000, 9000, "z"),
    ]
    out = retime_cues(cues, RetimeParams(max_borrow_ms=500))
    for a, b in zip(out, out[1:]):
        assert a.end_ms <= b.start_ms


def test_borrow_is_idempotent():
    cues = [_cue(1, 0, 2000, "x" * 80), _cue(2, 2000, 8000, "next")]
    p = RetimeParams(max_borrow_ms=500)
    once = retime_cues(cues, p)
    assert retime_cues(once, p) == once
