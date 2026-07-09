"""#364 slice 1 — ForcedSegmentWalker: resumable trickle, GPU-polite pause,
per-file error isolation, #157 Health record on clean completion + on cancel."""

from __future__ import annotations

import asyncio

import pytest


class _FakeGen:
    def __init__(self):
        self.calls = []

    async def process(self, canonical_path):
        self.calls.append(canonical_path)
        return {"status": "scanned", "n_spans": 1, "total_ms": 3000}


class _FakeHealth:
    def __init__(self):
        self.successes = []
        self.failures = []

    def record_success(self, name):
        self.successes.append(name)

    def record_failure(self, name, exc):
        self.failures.append((name, exc))


def _walker(gen, worklist, busy=None):
    from subarr import forced_segment_service as svc

    w = svc.ForcedSegmentWalker(generator=gen, worklist=lambda scope="library": worklist, busy_check=busy)
    return w


@pytest.mark.asyncio
async def test_walks_every_file_and_records_health(monkeypatch):
    from subarr import forced_segment_service as svc

    monkeypatch.setattr(svc, "_PER_FILE_SLEEP_S", 0)
    gen = _FakeGen()
    health = _FakeHealth()
    w = _walker(gen, ["TV/A.mkv", "TV/B.mkv"])
    w._health = health
    state = await w.start(scope="library")
    await w._task
    assert gen.calls == ["TV/A.mkv", "TV/B.mkv"]
    assert state.processed == 2 and state.found == 2 and state.status == "done"
    assert health.successes == ["forced-segment"]


@pytest.mark.asyncio
async def test_gpu_polite_pauses_while_busy(monkeypatch):
    from subarr import forced_segment_service as svc

    monkeypatch.setattr(svc, "_PER_FILE_SLEEP_S", 0)
    monkeypatch.setattr(svc, "_BUSY_SLEEP_S", 0)
    busy = {"v": True}
    gen = _FakeGen()
    w = _walker(gen, ["TV/A.mkv"], busy=lambda: busy["v"])
    task_state = await w.start(scope="library")
    await asyncio.sleep(0)
    assert gen.calls == []  # paused: no detect fired while busy
    busy["v"] = False
    await w._task
    assert gen.calls == ["TV/A.mkv"] and task_state.status == "done"


@pytest.mark.asyncio
async def test_per_file_error_isolated(monkeypatch):
    from subarr import forced_segment_service as svc

    monkeypatch.setattr(svc, "_PER_FILE_SLEEP_S", 0)

    class _Boom(_FakeGen):
        async def process(self, canonical_path):
            if canonical_path == "TV/A.mkv":
                raise RuntimeError("clip blew up")
            return await super().process(canonical_path)

    gen = _Boom()
    health = _FakeHealth()
    w = _walker(gen, ["TV/A.mkv", "TV/B.mkv"])
    w._health = health
    state = await w.start(scope="library")
    await w._task
    assert state.processed == 2 and len(state.errors) == 1
    assert state.status == "done" and health.successes == ["forced-segment"]  # run still clean
