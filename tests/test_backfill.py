"""#116 — throttled library-backfill: the selection/throttle CORE.

Borrowed from Sonarr_Backfiller's queue-depth throttle: drain the verified
coverage-gap backlog gently, holding subgen's queue at a target depth instead
of dumping the whole backlog at once.

This pins the PURE decision math (which gaps to enqueue NOW). The integration
— reading the live subgen queue depth, pulling the eligible (verified +
settle-window-passed) gaps, and ticking on the scheduler — is the documented
next step, deliberately NOT built here.
"""
from __future__ import annotations


def _bf():
    from subarr import backfill
    return backfill


def _cfg(**kw):
    bf = _bf()
    return bf.BackfillConfig(**{"enabled": True, "target_queue_depth": 3, "batch": 5, **kw})


GAPS = [f"gap{i}" for i in range(10)]


def test_disabled_selects_nothing():
    bf = _bf()
    assert bf.select_backfill_batch(GAPS, queue_depth=0, config=_cfg(enabled=False)) == []


def test_queue_at_or_above_target_selects_nothing():
    bf = _bf()
    assert bf.select_backfill_batch(GAPS, queue_depth=3, config=_cfg(target_queue_depth=3)) == []
    assert bf.select_backfill_batch(GAPS, queue_depth=5, config=_cfg(target_queue_depth=3)) == []


def test_fills_headroom_up_to_target():
    bf = _bf()
    # target 3, queue 1 → headroom 2 → enqueue 2 (batch 5 not the limit)
    out = bf.select_backfill_batch(GAPS, queue_depth=1, config=_cfg(target_queue_depth=3, batch=5))
    assert out == ["gap0", "gap1"]


def test_batch_caps_a_large_headroom():
    bf = _bf()
    # target 20, queue 0 → headroom 20, but batch 4 caps it
    out = bf.select_backfill_batch(GAPS, queue_depth=0, config=_cfg(target_queue_depth=20, batch=4))
    assert out == ["gap0", "gap1", "gap2", "gap3"]


def test_never_exceeds_available_gaps():
    bf = _bf()
    out = bf.select_backfill_batch(["only0", "only1"], queue_depth=0, config=_cfg(target_queue_depth=10, batch=10))
    assert out == ["only0", "only1"]


def test_empty_backlog_selects_nothing():
    bf = _bf()
    assert bf.select_backfill_batch([], queue_depth=0, config=_cfg()) == []


def test_config_defaults_are_safe_off():
    bf = _bf()
    # default config must be DISABLED — backfill never runs unless opted in
    assert bf.BackfillConfig().enabled is False
