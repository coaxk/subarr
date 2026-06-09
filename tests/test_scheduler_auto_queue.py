"""Tests for v1.2 batch 1: scheduler + auto-queue rules."""
from __future__ import annotations

import datetime
import time



# ───── _due() schedule logic ────────────────────────────────────────────


def test_due_interval_first_run_fires_immediately():
    from subarr.schedule_store import KIND_INTERVAL, ScheduleConfig
    from subarr.scheduler import _due
    sched = ScheduleConfig(
        name="t", enabled=True, kind=KIND_INTERVAL,
        interval_minutes=60, daily_hhmm="03:00", day_of_week=0,
        last_run_at=None, next_run_at=None, last_result=None,
    )
    assert _due(sched, datetime.datetime.now(), None) is True


def test_due_interval_not_yet():
    from subarr.schedule_store import KIND_INTERVAL, ScheduleConfig
    from subarr.scheduler import _due
    sched = ScheduleConfig(
        name="t", enabled=True, kind=KIND_INTERVAL,
        interval_minutes=60, daily_hhmm="03:00", day_of_week=0,
        last_run_at=time.time() - 30 * 60, next_run_at=None, last_result=None,
    )
    assert _due(sched, datetime.datetime.now(), sched.last_run_at) is False


def test_due_daily_fires_after_target_time():
    from subarr.schedule_store import KIND_DAILY, ScheduleConfig
    from subarr.scheduler import _due
    now = datetime.datetime(2026, 5, 27, 4, 0, 0)
    sched = ScheduleConfig(
        name="t", enabled=True, kind=KIND_DAILY,
        interval_minutes=0, daily_hhmm="03:00", day_of_week=0,
        last_run_at=None, next_run_at=None, last_result=None,
    )
    assert _due(sched, now, None) is True
    # If we ran 15 minutes ago, no fire.
    last = (now - datetime.timedelta(minutes=15)).timestamp()
    assert _due(sched, now, last) is False


def test_due_disabled_never_fires():
    from subarr.schedule_store import KIND_INTERVAL, ScheduleConfig
    from subarr.scheduler import _due
    sched = ScheduleConfig(
        name="t", enabled=False, kind=KIND_INTERVAL,
        interval_minutes=1, daily_hhmm="03:00", day_of_week=0,
        last_run_at=None, next_run_at=None, last_result=None,
    )
    assert _due(sched, datetime.datetime.now(), None) is False


# ───── auto_queue.evaluate ──────────────────────────────────────────────


def _item(score=300, lang="Korean", monitored=True, has_disk=False, tags=None,
          ep_id=1, canonical="TV/X/S01E01.mkv", title="X",
          verification_state="verified"):
    from subarr.coverage_engine import CoverageItem
    return CoverageItem(
        media_type="episode", title=title, episode_number="1x1",
        original_language=lang, monitored=monitored, tags=tags or [],
        canonical_path=canonical, has_sub_on_disk=has_disk,
        bazarr_episode_id=ep_id, score=score,
        verification_state=verification_state,
    )


def test_evaluate_skips_unverified_rows():
    """Probe-gate: auto-queue must never act on a row subarr hasn't probed,
    regardless of score — that's how un-probed files end up skipped by subgen."""
    from subarr.auto_queue import evaluate
    from subarr.schedule_store import AutoQueueRules, MODE_AUTO_RULES
    items = [
        _item(score=2000, verification_state="unprobed"),
        _item(score=2000, ep_id=2, verification_state="probe_failed"),
    ]
    rules = AutoQueueRules(mode=MODE_AUTO_RULES, min_score=0,
                           deny_languages=[], require_monitored=False)
    decisions = evaluate(items, rules)
    assert all(d.action == "skip" for d in decisions)
    assert all("unverified" in d.reason for d in decisions)


def test_evaluate_dashboard_mode_skips_everything():
    from subarr.auto_queue import evaluate
    from subarr.schedule_store import AutoQueueRules, MODE_DASHBOARD
    items = [_item(), _item(score=1500)]
    decisions = evaluate(items, AutoQueueRules(mode=MODE_DASHBOARD))
    assert all(d.action == "skip" for d in decisions)
    assert all("dashboard" in d.reason for d in decisions)


def test_evaluate_auto_rules_min_score():
    from subarr.auto_queue import evaluate
    from subarr.schedule_store import AutoQueueRules, MODE_AUTO_RULES
    items = [_item(score=100), _item(score=500)]
    rules = AutoQueueRules(mode=MODE_AUTO_RULES, min_score=200,
                           deny_languages=[], require_monitored=False)
    decisions = evaluate(items, rules)
    actions = [d.action for d in decisions]
    assert actions.count("queue") == 1
    assert actions.count("skip") == 1


def test_evaluate_skips_stale_disk():
    from subarr.auto_queue import evaluate
    from subarr.schedule_store import AutoQueueRules, MODE_AUTO_RULES
    items = [_item(has_disk=True, score=2000)]
    rules = AutoQueueRules(mode=MODE_AUTO_RULES, min_score=0,
                           deny_languages=[], require_monitored=False)
    decisions = evaluate(items, rules)
    assert decisions[0].action == "skip"
    assert "stale" in decisions[0].reason


def test_evaluate_caps_max_per_run():
    from subarr.auto_queue import evaluate
    from subarr.schedule_store import AutoQueueRules, MODE_AUTO_RULES
    items = [_item(score=300 + i, ep_id=i, title=f"X{i}") for i in range(10)]
    rules = AutoQueueRules(mode=MODE_AUTO_RULES, min_score=0, max_per_run=3,
                           deny_languages=[], require_monitored=False)
    decisions = evaluate(items, rules)
    queued = [d for d in decisions if d.action == "queue"]
    capped = [d for d in decisions if d.action == "skip" and "max_per_run" in d.reason]
    assert len(queued) == 3
    assert len(capped) == 7
    # Top 3 scores should be queued (descending).
    assert {d.item.score for d in queued} == {309, 308, 307}


def test_evaluate_deny_languages():
    from subarr.auto_queue import evaluate
    from subarr.schedule_store import AutoQueueRules, MODE_AUTO_RULES
    items = [_item(lang="English"), _item(lang="Korean")]
    rules = AutoQueueRules(mode=MODE_AUTO_RULES, min_score=0,
                           deny_languages=["English"], require_monitored=False)
    decisions = evaluate(items, rules)
    by_lang = {d.item.original_language: d for d in decisions}
    assert by_lang["English"].action == "skip"
    assert by_lang["Korean"].action == "queue"


# ───── HTTP routes ──────────────────────────────────────────────────────


def test_schedule_get_default(app_with_stub):
    r = app_with_stub.get("/api/schedule")
    assert r.status_code == 200
    body = r.json()
    sched = next(s for s in body["schedules"] if s["name"] == "coverage_walk")
    assert sched["enabled"] is False
    assert body["rules"]["mode"] == "dashboard"


def test_schedule_patch(app_with_stub):
    r = app_with_stub.patch("/api/schedule/coverage_walk",
                             json={"enabled": True, "kind": "interval",
                                   "interval_minutes": 120})
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["interval_minutes"] == 120


def test_rules_put(app_with_stub):
    r = app_with_stub.put("/api/schedule/rules",
                           json={"mode": "auto_rules", "min_score": 1000,
                                 "deny_languages": []})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "auto_rules"
    assert body["min_score"] == 1000
    assert body["deny_languages"] == []
