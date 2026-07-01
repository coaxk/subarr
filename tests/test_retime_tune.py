"""#359: pure re-timer parameter sweep over an SRT corpus."""

from __future__ import annotations

from subarr.retime_tune import param_grid, retime_sweep
from subarr.subtitle_retime import RetimeParams

# One hot sub (40 cps cue + a micro-cue) and one comfortable sub.
_HOT = (
    "1\n00:00:00,000 --> 00:00:02,000\n"
    "This is a very long translated line that crams far too many characters\n\n"
    "2\n00:00:20,000 --> 00:00:20,300\nhi\n"
)
_CALM = (
    "1\n00:00:00,000 --> 00:00:03,000\nHello there.\n\n2\n00:00:04,000 --> 00:00:07,000\nGeneral Kenobi.\n"
)


def test_param_grid_is_target_cps_x_min_cue():
    grid = param_grid()
    assert len(grid) == 9
    assert RetimeParams(target_cps=17.0, min_cue_ms=1000, min_gap_ms=100, max_cue_ms=7000) in grid
    assert all(p.min_gap_ms == 100 and p.max_cue_ms == 7000 for p in grid)


def test_sweep_has_baseline_first_then_one_row_per_combo():
    rows = retime_sweep([_HOT, _CALM], param_grid())
    assert rows[0].params is None  # baseline (no re-timing)
    assert len(rows) == 1 + len(param_grid())
    assert all(r.subs == 2 for r in rows)


def test_sweep_reduces_critical_cps_and_micro_cues_vs_baseline():
    rows = retime_sweep([_HOT, _CALM], param_grid())
    baseline = rows[0]
    treated = [r for r in rows if r.params is not None]
    # every combo should reduce (or hold) %over-critical and micro-cues, and add screen time.
    assert any(r.pct_over_critical < baseline.pct_over_critical for r in treated)
    assert all(r.micro_cues <= baseline.micro_cues for r in treated)
    assert all(r.too_long <= baseline.too_long for r in treated)  # cap prevents over-long
    changed = [r for r in treated if r.subs_changed > 0]
    assert changed and all(r.mean_added_ms > 0 for r in changed)


def test_sweep_leaves_comfortable_only_corpus_essentially_unchanged():
    rows = retime_sweep([_CALM], param_grid())
    baseline = rows[0]
    for r in rows[1:]:
        assert r.subs_changed == 0
        assert r.median_cps == baseline.median_cps


def test_sweep_lower_target_cps_reduces_median_more():
    grid = [
        RetimeParams(target_cps=20.0, min_cue_ms=1000, min_gap_ms=100, max_cue_ms=7000),
        RetimeParams(target_cps=15.0, min_cue_ms=1000, min_gap_ms=100, max_cue_ms=7000),
    ]
    rows = retime_sweep([_HOT], grid)
    at20 = next(r for r in rows if r.params and r.params.target_cps == 20.0)
    at15 = next(r for r in rows if r.params and r.params.target_cps == 15.0)
    assert at15.median_cps <= at20.median_cps  # aim lower → extend more → lower CPS
