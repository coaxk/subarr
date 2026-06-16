"""#234: run-now control over background loops.

MVP scope — list + status already exist (/api/health/tasks + #157 task_health).
This adds an on-demand trigger for the loops that safely support it. Tests pin
the registry (which jobs are runnable) and the runner (correct trigger fires,
absent component / unknown job is a no-op, never raises).
"""

from __future__ import annotations

import types

import pytest

from subarr import jobs


class TestCanRunNow:
    def test_known_runnable_jobs(self):
        assert jobs.can_run_now("update-checker")
        assert jobs.can_run_now("queue-feeder")

    def test_monitor_only_jobs_are_not_runnable(self):
        # these are periodic/self-driven — surfaced for status, no manual trigger
        assert not jobs.can_run_now("scheduler")
        assert not jobs.can_run_now("subgen-watchdog")
        assert not jobs.can_run_now("unknown-loop")


@pytest.mark.asyncio
async def test_run_update_checker_calls_refresh_now():
    calls = []

    class _Checker:
        async def refresh_now(self):
            calls.append("refresh")

    state = types.SimpleNamespace(update_checker=_Checker())
    assert await jobs.run_job(state, "update-checker") is True
    assert calls == ["refresh"]


@pytest.mark.asyncio
async def test_run_queue_feeder_calls_kick():
    kicks = []
    state = types.SimpleNamespace(queue_feeder=types.SimpleNamespace(kick=lambda: kicks.append(1)))
    assert await jobs.run_job(state, "queue-feeder") is True
    assert kicks == [1]


@pytest.mark.asyncio
async def test_unknown_job_is_false_not_error():
    assert await jobs.run_job(types.SimpleNamespace(), "nope") is False


@pytest.mark.asyncio
async def test_absent_component_is_false_not_error():
    # component missing on app.state (e.g. early boot) -> no-op, no crash
    state = types.SimpleNamespace(update_checker=None)
    assert await jobs.run_job(state, "update-checker") is False
