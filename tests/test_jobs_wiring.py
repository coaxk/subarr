"""#252: the loops publish their own run-now triggers.

The property under test is NO DRIFT. A run-now button whose trigger is
assembled separately from the loop's own call will keep working while quietly
running something different from what the schedule runs, and nothing about the
button's behaviour reveals it. So the tests below capture the arguments of the
scheduled refresh and of the triggered refresh and require them to be identical
rather than merely both present.
"""

from __future__ import annotations

import asyncio

import pytest

from subarr import jobs


class _RecordingCache:
    """Records every refresh call so the scheduled and triggered ones can be
    compared argument for argument."""

    def __init__(self, cached=None):
        self.calls = []
        self._cached = cached

    def get_cached(self):
        return self._cached

    async def refresh(self, *a, **k):
        self.calls.append((a, k))
        return None


class _Health:
    def __init__(self):
        self.successes = []

    def record_success(self, name, expected_interval_s=None):
        self.successes.append(name)

    def record_failure(self, name, err, expected_interval_s=None):
        pass


async def _wait_for(pred, timeout=5.0):
    for _ in range(int(timeout / 0.05)):
        if pred():
            return True
        await asyncio.sleep(0.05)
    return False


class TestCoverageCacheTrigger:
    @pytest.mark.asyncio
    async def test_triggered_refresh_matches_the_scheduled_one(self):
        from subarr.coverage_cache import background_refresh_loop

        cache = _RecordingCache(cached={"warm": True})  # skip the boot warm
        state = type("S", (), {})()
        health = _Health()
        task = asyncio.create_task(
            background_refresh_loop(
                cache=cache,
                bundle_provider=lambda: "BUNDLE",
                probe_store="PROBES",
                audio_lang_store="LANGS",
                probe_walker="WALKER",
                caps_provider=lambda: "CAPS",
                interval_s=1,
                health=health,
                app_state=state,
            )
        )
        try:
            assert await _wait_for(lambda: len(cache.calls) >= 1), "loop never refreshed"
            scheduled = cache.calls[-1]

            assert jobs.can_run_now("coverage-cache")
            assert await jobs.run_job(state, "coverage-cache") is True
            triggered = cache.calls[-1]
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert triggered == scheduled, (
            "run-now called refresh differently from the scheduled tick; "
            "the button and the schedule have drifted"
        )

    @pytest.mark.asyncio
    async def test_no_app_state_still_runs_the_loop(self):
        # Registration is additive. Callers that pass no app_state (tests, and
        # any embedding that does not want the control surface) keep working.
        from subarr.coverage_cache import background_refresh_loop

        cache = _RecordingCache(cached={"warm": True})
        health = _Health()
        task = asyncio.create_task(background_refresh_loop(cache=cache, interval_s=1, health=health))
        try:
            assert await _wait_for(lambda: bool(health.successes))
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


class TestDashboardCacheTrigger:
    @pytest.mark.asyncio
    async def test_triggered_refresh_matches_the_scheduled_one(self):
        from subarr.dashboard_cache import background_refresh_loop as dash_loop

        cache = _RecordingCache()
        state = type("S", (), {})()
        health = _Health()

        def _build():
            return {"built": True}

        task = asyncio.create_task(
            dash_loop(
                cache=cache,
                build_fn=_build,
                interval_s=1,
                health=health,
                app_state=state,
            )
        )
        try:
            assert await _wait_for(lambda: len(cache.calls) >= 1), "loop never refreshed"
            scheduled = cache.calls[-1]

            assert jobs.can_run_now("dashboard-cache")
            assert await jobs.run_job(state, "dashboard-cache") is True
            triggered = cache.calls[-1]
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert triggered == scheduled


class TestClassLoopRunOnce:
    """SubgenWatchdog and CompletionWatcher already had a single-cycle method
    each (_probe_once, _tick). run_once() is a public alias so app.py can
    register a trigger without reaching into a private, and so the trigger is
    provably the same cycle the loop runs."""

    @pytest.mark.asyncio
    async def test_subgen_watchdog_run_once_probes(self):
        from subarr.subgen_watchdog import SubgenWatchdog

        wd = SubgenWatchdog.__new__(SubgenWatchdog)
        probes = []

        async def _probe():
            probes.append(1)

        wd._probe_once = _probe
        await wd.run_once()
        assert probes == [1]

    @pytest.mark.asyncio
    async def test_completion_watcher_run_once_ticks(self):
        from subarr.completion_watcher import CompletionWatcher

        cw = CompletionWatcher.__new__(CompletionWatcher)
        ticks = []

        async def _tick():
            ticks.append(1)

        cw._tick = _tick
        await cw.run_once()
        assert ticks == [1]
