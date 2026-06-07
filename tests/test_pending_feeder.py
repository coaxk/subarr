"""#66/#116 slice 2: pending-queue feeder. Depth-aware draining, pause,
shared-queue back-off + dedup, submit-failure isolation, priority order.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from subarr.migrate import run_migrations
from subarr.pending_queue import PendingQueueStore, STATUS_ERROR, STATUS_SUBMITTED
from subarr.pending_feeder import PendingQueueFeeder
from subarr.paths import canonical_to_subgen_batch
from subarr.subgen_client import SubgenUnavailable


class FakeSubgen:
    def __init__(self, queued=None, processing=None, raise_exc=None):
        self._queued = queued or []
        self._processing = processing or []
        self._raise = raise_exc

    async def queue(self):
        if self._raise:
            raise self._raise
        return {
            "queued": self._queued,
            "processing": self._processing,
            "queued_count": len(self._queued),
            "processing_count": len(self._processing),
        }


class SubmitRecorder:
    def __init__(self, fail_paths=None):
        self.calls = []
        self._fail = set(fail_paths or [])

    async def __call__(self, job):
        self.calls.append(job.canonical_path)
        if job.canonical_path in self._fail:
            raise RuntimeError("subgen rejected")


@pytest.fixture
def store(tmp_path: Path) -> PendingQueueStore:
    db = tmp_path / "subarr.db"
    run_migrations(db)
    return PendingQueueStore(db)


def _feeder(store, subgen, submit, *, target=2, paused=False):
    return PendingQueueFeeder(
        store=store, subgen_provider=lambda: subgen, submit_job=submit,
        target_depth_provider=lambda: target, paused_provider=lambda: paused,
    )


@pytest.mark.asyncio
async def test_feeds_up_to_target_depth(store):
    for i in range(5):
        store.enqueue(f"TV/e{i}.mkv", source="gaps")
    rec = SubmitRecorder()
    n = await _feeder(store, FakeSubgen(), rec, target=2).tick()
    assert n == 2
    assert len(rec.calls) == 2
    assert store.count_by_status().get(STATUS_SUBMITTED) == 2


@pytest.mark.asyncio
async def test_respects_pause(store):
    store.enqueue("TV/a.mkv", source="manual")
    rec = SubmitRecorder()
    n = await _feeder(store, FakeSubgen(), rec, target=2, paused=True).tick()
    assert n == 0 and rec.calls == []


@pytest.mark.asyncio
async def test_backs_off_when_subgen_full_of_foreign_work(store):
    store.enqueue("TV/a.mkv", source="gaps")
    # subgen already has 2 foreign items, target is 2 → no room
    foreign = FakeSubgen(queued=[{"path": "/media/Other/x.mkv"}],
                         processing=[{"path": "/media/Other/y.mkv"}])
    rec = SubmitRecorder()
    n = await _feeder(store, foreign, rec, target=2).tick()
    assert n == 0 and rec.calls == []


@pytest.mark.asyncio
async def test_partial_room_fills_only_the_gap(store):
    store.enqueue("TV/a.mkv", source="gaps")
    store.enqueue("TV/b.mkv", source="gaps")
    foreign = FakeSubgen(processing=[{"path": "/media/Other/y.mkv"}])  # 1 used, target 2
    rec = SubmitRecorder()
    n = await _feeder(store, foreign, rec, target=2).tick()
    assert n == 1 and len(rec.calls) == 1


@pytest.mark.asyncio
async def test_dedups_path_already_in_subgen(store):
    job = store.enqueue("TV/dup.mkv", source="gaps")
    sg_path = canonical_to_subgen_batch("TV/dup.mkv")
    # the same file is already queued on subgen (foreign-submitted)
    subgen = FakeSubgen(queued=[{"path": sg_path}])
    rec = SubmitRecorder()
    n = await _feeder(store, subgen, rec, target=2).tick()
    # adopted as submitted, NOT re-sent through submit_job
    assert rec.calls == []
    assert store.get(job.id).status == STATUS_SUBMITTED


@pytest.mark.asyncio
async def test_submit_failure_isolated_and_marks_error(store):
    store.enqueue("TV/bad.mkv", source="gaps")
    store.enqueue("TV/good.mkv", source="gaps")
    rec = SubmitRecorder(fail_paths={"TV/bad.mkv"})
    n = await _feeder(store, FakeSubgen(), rec, target=2).tick()
    # bad one errors (doesn't consume a slot), good one still submits
    assert "TV/good.mkv" in rec.calls
    statuses = {j.canonical_path: j.status for j in store.list()}
    assert statuses["TV/bad.mkv"] == STATUS_ERROR
    assert statuses["TV/good.mkv"] == STATUS_SUBMITTED


@pytest.mark.asyncio
async def test_subgen_unavailable_is_soft_skip(store):
    store.enqueue("TV/a.mkv", source="gaps")
    subgen = FakeSubgen(raise_exc=SubgenUnavailable("down"))
    rec = SubmitRecorder()
    n = await _feeder(store, subgen, rec, target=2).tick()
    assert n == 0 and rec.calls == []  # no crash, nothing submitted


@pytest.mark.asyncio
async def test_priority_order_manual_before_backfill(store):
    store.enqueue("TV/back.mkv", source="backfill")
    store.enqueue("TV/man.mkv", source="manual")
    rec = SubmitRecorder()
    await _feeder(store, FakeSubgen(), rec, target=1).tick()
    assert rec.calls == ["TV/man.mkv"]  # manual drained first


@pytest.mark.asyncio
async def test_empty_pending_no_submit(store):
    rec = SubmitRecorder()
    n = await _feeder(store, FakeSubgen(), rec, target=3).tick()
    assert n == 0
