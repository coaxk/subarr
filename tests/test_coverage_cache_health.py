"""Regression: the coverage-cache health staleness threshold must cover the
SLEEP plus the refresh build time. A flat interval_s marked the pill stale for
the ~60-90s of every refresh (false 'coverage cache' warning, dogfood 2026-06-20).
"""

from __future__ import annotations

import asyncio

import pytest

from subarr.coverage_cache import background_refresh_loop


class _FakeCache:
    def get_cached(self):
        return {"cached": True}  # non-None → skip the boot warm path

    async def refresh(self, *a, **k):
        return None


class _FakeHealth:
    def __init__(self):
        self.successes = []

    def record_success(self, name, expected_interval_s=None):
        self.successes.append((name, expected_interval_s))

    def record_failure(self, name, err, expected_interval_s=None):
        pass


@pytest.mark.asyncio
async def test_health_threshold_covers_refresh_duration():
    health = _FakeHealth()
    task = asyncio.create_task(background_refresh_loop(cache=_FakeCache(), interval_s=1, health=health))
    for _ in range(50):  # wait for the first post-sleep refresh to record success
        if health.successes:
            break
        await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert health.successes, "loop never recorded a success"
    name, expected_interval_s = health.successes[0]
    assert name == "coverage-cache"
    # threshold = 2x interval so a 60-90s refresh on top of the sleep never
    # false-flags the pill stale.
    assert expected_interval_s == 2
