"""#234: run-now control over background loops.

MVP scope — list + status already exist (/api/health/tasks + #157 task_health).
This adds an on-demand trigger for the loops that safely support it. Tests pin
the registry (which jobs are runnable) and the runner (correct trigger fires,
absent component / unknown job is a no-op, never raises).
"""

from __future__ import annotations

import asyncio
import logging
import types

import pytest

from subarr import jobs


class TestCanRunNow:
    def test_known_runnable_jobs(self):
        assert jobs.can_run_now("update-checker")
        assert jobs.can_run_now("queue-feeder")

    def test_monitor_only_jobs_are_not_runnable(self):
        # Periodic/self-driven, surfaced for status with no manual trigger.
        # subgen-watchdog USED to be listed here and moved to runnable in the
        # #252 slice: it re-polls subgen reachability, which is the same
        # shape as the other refresh triggers, and telemetry (#479) says half
        # of genuine installs report subgen unreachable, so "check it now" is
        # the button those users actually want.
        assert not jobs.can_run_now("scheduler")
        assert not jobs.can_run_now("db-integrity")
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


# ─── #252 slice: expand run-now beyond the two MVP loops ────────────────────


class TestExpandedRunnableSet:
    """#252 item 2. The MVP shipped run-now for two loops. These four are the
    refresh-style loops whose trigger is safe to expose."""

    def test_refresh_loops_are_runnable(self):
        assert jobs.can_run_now("coverage-cache")
        assert jobs.can_run_now("dashboard-cache")
        assert jobs.can_run_now("subgen-watchdog")
        assert jobs.can_run_now("completion-watcher")

    def test_scheduler_stays_monitor_only(self):
        # DELIBERATE exclusion, not an oversight. Every other runnable loop
        # refreshes a cache or re-polls a probe: running it early costs a
        # request and changes nothing else. A scheduler tick FIRES DUE JOBS,
        # so a button labelled the same as the others would start real scans.
        # If it is ever added it needs its own confirm, not this registry.
        assert not jobs.can_run_now("scheduler")

    def test_db_integrity_stays_monitor_only(self):
        # Also deliberate: it already has a dedicated manual endpoint from the
        # #291 Slice B deep-integrity work. A second trigger here would be a
        # divergent copy of the same control.
        assert not jobs.can_run_now("db-integrity")

    def test_unknown_job_still_not_runnable(self):
        assert not jobs.can_run_now("unknown-loop")


class TestSelfRegisteredTriggers:
    """The loops above cannot be driven from app.state alone: coverage-cache's
    refresh() takes bundle, probe_store, audio_lang_store, probe_walker and
    caps_provider, several of which are closures inside the loop function.

    Rebuilding that argument list here would be a SECOND COPY of the loop's
    wiring, free to drift, and the drift would be invisible: the button would
    keep working while quietly running something other than what the schedule
    runs. So the loop publishes its own no-arg trigger and jobs.py only looks
    it up."""

    @pytest.mark.asyncio
    async def test_registered_trigger_fires(self):
        fired = []

        async def _trigger():
            fired.append(1)

        state = types.SimpleNamespace()
        jobs.register_trigger(state, "coverage-cache", _trigger)
        assert await jobs.run_job(state, "coverage-cache") is True
        assert fired == [1]

    @pytest.mark.asyncio
    async def test_declared_but_unregistered_is_false_not_error(self):
        # Early boot: the task is declared runnable so the button renders, but
        # its loop has not started and published a trigger yet. That is the
        # 409 path, not a crash.
        state = types.SimpleNamespace()
        assert await jobs.run_job(state, "coverage-cache") is False

    @pytest.mark.asyncio
    async def test_triggers_do_not_leak_between_app_states(self):
        # A module-level registry would make one test (or one app instance)
        # visible to the next. Registration must hang off app.state.
        async def _trigger():
            pass

        state_a = types.SimpleNamespace()
        jobs.register_trigger(state_a, "dashboard-cache", _trigger)
        state_b = types.SimpleNamespace()
        assert await jobs.run_job(state_b, "dashboard-cache") is False

    @pytest.mark.asyncio
    async def test_a_sync_trigger_is_accepted_too(self):
        # queue-feeder's existing trigger is sync (kick()). Registration should
        # not force every owner to wrap theirs in a coroutine.
        fired = []
        state = types.SimpleNamespace()
        jobs.register_trigger(state, "subgen-watchdog", lambda: fired.append(1))
        assert await jobs.run_job(state, "subgen-watchdog") is True
        assert fired == [1]


class TestRunJobNeverRaises:
    """run_job's docstring has always promised 'Never raises -- a control action
    must not 500 the page'. It did not hold: nothing caught an exception from
    the trigger itself, and neither does the endpoint, so a throwing trigger
    returned a 500.

    This mattered little when the only triggers were an update poll and a
    feeder kick. It matters now: a coverage refresh calls out to Bazarr,
    Sonarr and Plex, so a trigger that raises is an ordinary Tuesday."""

    @pytest.mark.asyncio
    async def test_raising_registered_trigger_is_false_not_an_exception(self):
        async def _boom():
            raise RuntimeError("bazarr refused the connection")

        state = types.SimpleNamespace()
        jobs.register_trigger(state, "coverage-cache", _boom)
        assert await jobs.run_job(state, "coverage-cache") is False

    @pytest.mark.asyncio
    async def test_raising_builtin_trigger_is_false_not_an_exception(self):
        class _Checker:
            async def refresh_now(self):
                raise RuntimeError("upstream 503")

        state = types.SimpleNamespace(update_checker=_Checker())
        assert await jobs.run_job(state, "update-checker") is False

    @pytest.mark.asyncio
    async def test_cancellation_still_propagates(self):
        # A blanket except would swallow CancelledError and break shutdown.
        async def _cancelled():
            raise asyncio.CancelledError()

        state = types.SimpleNamespace()
        jobs.register_trigger(state, "coverage-cache", _cancelled)
        with pytest.raises(asyncio.CancelledError):
            await jobs.run_job(state, "coverage-cache")


class TestRunJobDoesNotLeakSecrets:
    """A failing trigger gets logged, and subarr serves its own recent log over
    /api/logs/recent (#414) on an install that may have no auth. Integrations
    carry their credential in the request URL (Tautulli ?apikey=, Plex
    ?X-Plex-Token=) and httpx puts the URL in the exception text, so logging a
    trigger failure verbatim would publish the key.

    task_health already redacts for exactly this reason before storing a
    traceback. run_job has to do the same, and must reuse that one regex rather
    than carry a second copy that can drift out of agreement with it."""

    @pytest.mark.asyncio
    async def test_credential_in_trigger_error_is_redacted(self, caplog):
        async def _boom():
            raise RuntimeError("GET http://tautulli:8181/api/v2?apikey=s3cr3tvalue&cmd=x failed")

        state = types.SimpleNamespace()
        jobs.register_trigger(state, "coverage-cache", _boom)
        with caplog.at_level(logging.WARNING):
            assert await jobs.run_job(state, "coverage-cache") is False

        logged = caplog.text
        assert "s3cr3tvalue" not in logged
        assert "redacted" in logged
        # still useful for debugging: the endpoint and task are intact
        assert "coverage-cache" in logged

    def test_reuses_the_task_health_redactor(self):
        # Not a second regex. If these ever diverge, one of the two surfaces
        # starts leaking while the other looks fine.
        from subarr import task_health

        assert jobs.redact_secrets is task_health.redact_secrets
