"""Tests for #104: coverage-refresh debounce/coalesce + parallel rglob.

Two concerns under test:

1. Debounce / coalesce of the event-driven refresh kicks. A burst of
   `request_refresh()` calls must collapse into at most one in-flight
   build + at most one queued follow-up, and must respect a minimum
   interval between builds (the config knob). We never want N events to
   trigger N full 60-90s builds.

2. The per-series .srt rglob fan-out must run with bounded concurrency
   (parallel, but capped) rather than strictly one-at-a-time.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from subarr import coverage_cache
from subarr.coverage_cache import CoverageCache


@pytest.fixture
def cache(tmp_path):
    return CoverageCache(tmp_path / "cov.db")


def _patch_build(monkeypatch, *, calls: list, delay: float = 0.05):
    """Patch build_coverage so refresh() does a cheap fake build that
    records each invocation and sleeps `delay` to simulate work."""
    async def _fake_build(bundle, **kwargs):
        calls.append(time.monotonic())
        await asyncio.sleep(delay)

        class _Report:
            def to_dict(self):
                return {"items": [], "totals": {}, "sources": {}}
        return _Report()

    import subarr.coverage_engine as ce
    monkeypatch.setattr(ce, "build_coverage", _fake_build)


# ─── Debounce / coalesce ────────────────────────────────────────────


def test_burst_of_triggers_coalesces_to_at_most_two_builds(cache, monkeypatch):
    """N rapid triggers while a build is in flight collapse to one
    in-flight build + one queued follow-up (so exactly 2 builds, not N)."""
    calls: list = []
    _patch_build(monkeypatch, calls=calls, delay=0.1)

    async def _run():
        # Fire 10 triggers in a tight burst. The first starts a build;
        # the rest arrive while it runs and must coalesce into a single
        # follow-up.
        for _ in range(10):
            cache.request_refresh(None, None, None)
            await asyncio.sleep(0)  # let the scheduler pick up each call
        # Wait for everything to drain.
        for _ in range(100):
            await asyncio.sleep(0.05)
            if not cache.is_refreshing() and not cache.has_pending_refresh():
                break
        await asyncio.sleep(0.05)

    asyncio.run(_run())
    assert len(calls) <= 2, f"expected ≤2 builds from a burst, got {len(calls)}"
    assert len(calls) >= 1, "a refresh must still happen"


def test_request_refresh_respects_min_interval(cache, monkeypatch):
    """A second request shortly after a completed build is debounced —
    it does not immediately fire another full build."""
    calls: list = []
    _patch_build(monkeypatch, calls=calls, delay=0.01)
    cache.set_min_interval_s(0.5)

    async def _run():
        cache.request_refresh(None, None, None)
        # Wait for the first build to finish.
        for _ in range(50):
            await asyncio.sleep(0.02)
            if calls and not cache.is_refreshing():
                break
        assert len(calls) == 1
        # Immediately request again — inside the 0.5s window. Must NOT
        # produce a second immediate build.
        cache.request_refresh(None, None, None)
        await asyncio.sleep(0.1)
        assert len(calls) == 1, "second request inside window should be debounced"

    asyncio.run(_run())


def test_min_interval_default_from_config(monkeypatch, tmp_path):
    """The debounce window defaults from settings.coverage_refresh_min_interval_s."""
    from subarr import config
    c = CoverageCache(tmp_path / "c.db")
    # Default knob present and applied.
    assert c.min_interval_s == config.load().coverage_refresh_min_interval_s


# ─── Parallel rglob ─────────────────────────────────────────────────


def test_srt_index_scan_runs_concurrently(monkeypatch):
    """_build_srt_index_parallel must run scans with >1 in flight at once
    (parallel) but never exceed the concurrency cap."""
    import subarr.coverage_engine as ce

    in_flight = 0
    max_in_flight = 0

    def _slow_scan(canonical: str):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        time.sleep(0.05)
        in_flight -= 1
        return [f"{canonical}/sub.srt"]

    monkeypatch.setattr(ce, "_scan_for_srt_recursive", _slow_scan)

    dirs = [f"series{i}" for i in range(12)]
    cap = 4
    result = asyncio.run(ce._build_srt_index_parallel(dirs, cap=cap))

    assert max_in_flight > 1, "scans must run concurrently, not serially"
    assert max_in_flight <= cap, f"concurrency must be capped at {cap}, saw {max_in_flight}"
    assert set(result.keys()) == set(dirs)
    assert result["series0"] == ["series0/sub.srt"]


def test_srt_index_handles_empty_and_dedupes(monkeypatch):
    import subarr.coverage_engine as ce

    seen: list = []

    def _scan(canonical: str):
        seen.append(canonical)
        return []

    monkeypatch.setattr(ce, "_scan_for_srt_recursive", _scan)
    # Duplicate + empty dirs must be deduped and skipped.
    result = asyncio.run(
        ce._build_srt_index_parallel(["a", "a", "", "b"], cap=4)
    )
    assert set(result.keys()) == {"a", "b"}
    assert sorted(seen) == ["a", "b"]
