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
    out = bf.select_backfill_batch(
        ["only0", "only1"], queue_depth=0, config=_cfg(target_queue_depth=10, batch=10)
    )
    assert out == ["only0", "only1"]


def test_empty_backlog_selects_nothing():
    bf = _bf()
    assert bf.select_backfill_batch([], queue_depth=0, config=_cfg()) == []


def test_config_defaults_are_safe_off():
    bf = _bf()
    # default config must be DISABLED — backfill never runs unless opted in
    assert bf.BackfillConfig().enabled is False


# ── eligible_backfill_items: the queue-authority backlog selector (#66/#116) ──


def _item(title, *, lang="Korean", score=500, vstate="verified", mon=True, disk=False, emb=None, path=None):
    from subarr.coverage_engine import CoverageItem

    return CoverageItem(
        media_type="episode",
        title=title,
        original_language=lang,
        score=score,
        verification_state=vstate,
        monitored=mon,
        has_sub_on_disk=disk,
        embedded_en=emb,
        canonical_path=path or f"TV/{title}",
        file_canonical_path=path or f"TV/{title}/ep.mkv",
    )


def _rules(**kw):
    from subarr.schedule_store import AutoQueueRules, MODE_DASHBOARD

    base = dict(
        mode=MODE_DASHBOARD,
        min_score=200,
        max_per_run=5,
        deny_languages=["English"],
        require_monitored=True,
        skip_stale_disk=True,
        skip_embedded_en=True,
    )
    base.update(kw)
    return AutoQueueRules(**base)


def test_eligible_returns_verified_nonenglish_gaps():
    bf = _bf()
    items = [_item("A"), _item("B")]
    out = bf.eligible_backfill_items(items, _rules())
    assert {i.title for i in out} == {"A", "B"}


def test_eligible_ignores_max_per_run_cap():
    bf = _bf()
    items = [_item(f"S{i}") for i in range(50)]
    # rules cap is 5, but backfill loads the WHOLE eligible backlog
    out = bf.eligible_backfill_items(items, _rules(max_per_run=5))
    assert len(out) == 50


def test_eligible_works_even_in_dashboard_mode():
    bf = _bf()
    # dashboard mode would make auto-queue skip everything; backfill forces
    # auto_rules internally so an explicit backfill still works.
    out = bf.eligible_backfill_items([_item("A")], _rules(mode="dashboard"))
    assert len(out) == 1


def test_eligible_respects_quality_filters():
    bf = _bf()
    items = [
        _item("good"),  # eligible
        _item("english", lang="English"),  # deny_languages
        _item("lowscore", score=10),  # < min_score
        _item("hassub", disk=True),  # stale disk
        _item("embedded", emb="EN"),  # embedded EN
        _item("unmonitored", mon=False),  # not monitored
        _item("unprobed", vstate="unprobed"),  # probe-gate
    ]
    out = bf.eligible_backfill_items(items, _rules())
    assert {i.title for i in out} == {"good"}


def test_eligible_excludes_in_flight():
    bf = _bf()
    items = [_item("A"), _item("B")]  # default file_canonical_path = TV/<t>/ep.mkv
    out = bf.eligible_backfill_items(items, _rules(), in_flight_paths={"TV/A/ep.mkv"})
    assert {i.title for i in out} == {"B"}
