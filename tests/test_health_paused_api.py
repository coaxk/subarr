"""#666: `/api/health/tasks` reports paused loops through the real app."""

from __future__ import annotations

import pytest

WALK_OFF = {"label": "coverage walk off", "href": "/rules"}
FEED_PAUSED = {"label": "feed paused", "href": "/queue"}


def _tasks(client):
    return {t["task_name"]: t for t in client.get("/api/health/tasks").json()["tasks"]}


@pytest.mark.usefixtures("subarr_env")
def test_fresh_boot_reports_only_the_seeded_walk_as_off(app_with_stub):
    r = app_with_stub.get("/api/health/tasks")
    assert r.status_code == 200
    tasks = {t["task_name"]: t for t in r.json()["tasks"]}

    assert tasks["scheduler"]["paused"] == WALK_OFF
    assert tasks["queue-feeder"]["paused"] is None
    for name, task in tasks.items():
        assert "paused" in task
        if name != "scheduler":
            assert task["paused"] is None


@pytest.mark.usefixtures("subarr_env")
def test_pausing_the_feed_is_reported_without_changing_health(app_with_stub):
    before = app_with_stub.get("/api/health/tasks").json()
    before_tasks = {t["task_name"]: t for t in before["tasks"]}

    r = app_with_stub.post("/api/queue/control", json={"paused": True})
    assert r.status_code == 200

    after = app_with_stub.get("/api/health/tasks").json()
    after_tasks = {t["task_name"]: t for t in after["tasks"]}

    assert after_tasks["queue-feeder"]["paused"] == FEED_PAUSED
    assert after["any_unhealthy"] == before["any_unhealthy"]
    assert after["unhealthy_count"] == before["unhealthy_count"]
    assert after_tasks["queue-feeder"]["is_unhealthy"] == before_tasks["queue-feeder"]["is_unhealthy"]
    assert after_tasks["queue-feeder"]["can_run_now"] == before_tasks["queue-feeder"]["can_run_now"]


@pytest.mark.usefixtures("subarr_env")
def test_resuming_the_feed_clears_the_paused_marker(app_with_stub):
    app_with_stub.post("/api/queue/control", json={"paused": True})
    r = app_with_stub.post("/api/queue/control", json={"paused": False})
    assert r.status_code == 200

    assert _tasks(app_with_stub)["queue-feeder"]["paused"] is None


@pytest.mark.usefixtures("subarr_env")
def test_toggling_the_walk_switches_the_paused_marker(app_with_stub):
    r = app_with_stub.patch("/api/schedule/coverage_walk", json={"enabled": True})
    assert r.status_code == 200
    assert _tasks(app_with_stub)["scheduler"]["paused"] is None

    r = app_with_stub.patch("/api/schedule/coverage_walk", json={"enabled": False})
    assert r.status_code == 200
    assert _tasks(app_with_stub)["scheduler"]["paused"] == WALK_OFF


@pytest.mark.usefixtures("subarr_env")
def test_missing_schedule_store_reports_nothing_paused(app_with_stub, monkeypatch):
    from subarr.app import app

    store = app.state.schedule
    monkeypatch.delattr(app.state, "schedule")
    try:
        r = app_with_stub.get("/api/health/tasks")
        assert r.status_code == 200

        for task in r.json()["tasks"]:
            assert task["paused"] is None
    finally:
        app.state.schedule = store


@pytest.mark.usefixtures("subarr_env")
def test_a_failing_store_read_keeps_the_other_pause(app_with_stub, monkeypatch):
    from subarr.app import app

    def _raise():
        raise RuntimeError("store read failed")

    monkeypatch.setattr(app.state.schedule, "get_rules", _raise)
    r = app_with_stub.get("/api/health/tasks")
    assert r.status_code == 200
    tasks = {t["task_name"]: t for t in r.json()["tasks"]}

    assert tasks["queue-feeder"]["paused"] is None
    assert tasks["scheduler"]["paused"] == WALK_OFF
