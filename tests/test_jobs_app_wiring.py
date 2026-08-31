"""#252: the app.py wiring, exercised through a real lifespan boot.

The unit tests prove the registry and the triggers in isolation. Nothing there
touches app.py, so a trigger that is never registered at start-up would pass
every one of them while the button 409s for every user. This boots the app and
asks the running instance.
"""

from __future__ import annotations

import pytest

from subarr import jobs


@pytest.mark.usefixtures("subarr_env")
def test_runnable_tasks_are_advertised_by_the_api(app_with_stub):
    r = app_with_stub.get("/api/health/tasks")
    assert r.status_code == 200
    tasks = {t["task_name"]: t for t in r.json()["tasks"]}

    # Every task the registry declares runnable and that the app actually
    # registered must advertise the button.
    for name in ("coverage-cache", "dashboard-cache", "subgen-watchdog", "completion-watcher"):
        assert name in tasks, f"{name} missing from /api/health/tasks entirely"
        assert tasks[name]["can_run_now"] is True, f"{name} should offer run-now"

    # And the deliberate exclusions must NOT advertise it.
    for name in ("scheduler", "db-integrity"):
        if name in tasks:
            assert tasks[name]["can_run_now"] is False, f"{name} is monitor-only"


@pytest.mark.usefixtures("subarr_env")
def test_lifespan_actually_registered_the_triggers(app_with_stub):
    # The failure this catches: can_run_now is a static set, so the button
    # renders whether or not anything registered a trigger. Without this the
    # buttons would look correct and 409 forever.
    from subarr.app import app

    triggers = getattr(app.state, "job_triggers", None)
    assert triggers, "no triggers registered during lifespan"
    for name in ("coverage-cache", "dashboard-cache", "subgen-watchdog", "completion-watcher"):
        assert name in triggers, f"{name} declared runnable but never registered a trigger"
        assert callable(triggers[name])


@pytest.mark.usefixtures("subarr_env")
def test_every_self_registered_job_actually_registers(app_with_stub):
    # Pins the two halves together. Adding a name to SELF_REGISTERED without
    # wiring its loop is the exact mistake this whole file exists to catch.
    from subarr.app import app

    triggers = getattr(app.state, "job_triggers", None) or {}
    missing = sorted(jobs.SELF_REGISTERED - set(triggers))
    assert not missing, f"declared self-registered but never wired: {missing}"


@pytest.mark.usefixtures("subarr_env")
def test_monitor_only_job_is_rejected_with_400(app_with_stub):
    r = app_with_stub.post("/api/health/tasks/scheduler/run")
    assert r.status_code == 400


@pytest.mark.usefixtures("subarr_env")
def test_unknown_job_is_rejected_with_400(app_with_stub):
    r = app_with_stub.post("/api/health/tasks/not-a-real-loop/run")
    assert r.status_code == 400
