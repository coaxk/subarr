"""#66/#116 slice 2: pending-queue feeder. Depth-aware draining, pause,
shared-queue back-off + dedup, submit-failure isolation, priority order.
"""

from __future__ import annotations

import asyncio
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
        store=store,
        subgen_provider=lambda: subgen,
        submit_job=submit,
        target_depth_provider=lambda: target,
        paused_provider=lambda: paused,
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
    foreign = FakeSubgen(queued=[{"path": "/media/Other/x.mkv"}], processing=[{"path": "/media/Other/y.mkv"}])
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
    await _feeder(store, subgen, rec, target=2).tick()
    # adopted as submitted, NOT re-sent through submit_job
    assert rec.calls == []
    assert store.get(job.id).status == STATUS_SUBMITTED


@pytest.mark.asyncio
async def test_submit_failure_isolated_and_marks_error(store):
    store.enqueue("TV/bad.mkv", source="gaps")
    store.enqueue("TV/good.mkv", source="gaps")
    rec = SubmitRecorder(fail_paths={"TV/bad.mkv"})
    await _feeder(store, FakeSubgen(), rec, target=2).tick()
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


@pytest.mark.asyncio
async def test_no_overfeed_while_subgen_lags(store):
    # subgen's /batch is async — submitted jobs don't appear in /queue right
    # away. The feeder must count its own in-flight submissions so a second tick
    # (still seeing an empty subgen queue) doesn't re-fill the same slots and
    # overshoot target depth. This is the over-feed race (Processing+2 Queued
    # instead of Processing+1 Queued+1 Pending).
    for i in range(5):
        store.enqueue(f"TV/e{i}.mkv", source="gaps")
    lagging = FakeSubgen()  # always reports empty (nothing surfaced yet)
    rec = SubmitRecorder()
    feeder = _feeder(store, lagging, rec, target=2)
    n1 = await feeder.tick()
    n2 = await feeder.tick()
    n3 = await feeder.tick()
    assert n1 == 2  # first tick fills to target
    assert n2 == 0 and n3 == 0  # no over-feed despite empty /queue
    assert len(rec.calls) == 2
    assert store.count_by_status().get(STATUS_SUBMITTED) == 2


@pytest.mark.asyncio
async def test_inflight_reservation_released_when_job_surfaces(store):
    # Once a submitted job appears in subgen's queue, its in-flight reservation
    # is released (handed off to the real depth count) — no double-counting, and
    # a freed slot is refilled.
    store.enqueue("TV/a.mkv", source="gaps")
    store.enqueue("TV/b.mkv", source="gaps")
    store.enqueue("TV/c.mkv", source="gaps")
    sub = FakeSubgen()
    rec = SubmitRecorder()
    feeder = _feeder(store, sub, rec, target=2)
    assert await feeder.tick() == 2  # submit a, b → inflight {a,b}
    sub._queued = [{"path": canonical_to_subgen_batch("TV/a.mkv")}]  # a surfaces
    assert await feeder.tick() == 0  # depth1(a)+inflight1(b)=2
    sub._queued = [{"path": canonical_to_subgen_batch("TV/b.mkv")}]  # a done, b surfaced
    assert await feeder.tick() == 1  # depth1(b)+inflight0 → submit c
    assert rec.calls == ["TV/a.mkv", "TV/b.mkv", "TV/c.mkv"]


@pytest.mark.asyncio
async def test_inflight_reservation_expires_after_grace(store, monkeypatch):
    # A submitted job that never surfaces (e.g. completed faster than a tick)
    # must release its slot after the grace window, or the feeder under-feeds
    # forever.
    import subarr.pending_feeder as pf

    clock = {"t": 1000.0}
    monkeypatch.setattr(pf.time, "monotonic", lambda: clock["t"])
    store.enqueue("TV/a.mkv", source="gaps")
    store.enqueue("TV/b.mkv", source="gaps")
    sub = FakeSubgen()  # always empty
    rec = SubmitRecorder()
    feeder = _feeder(store, sub, rec, target=1)
    assert await feeder.tick() == 1  # submit a → inflight {a}
    assert await feeder.tick() == 0  # a still reserved → no submit
    clock["t"] += pf.INFLIGHT_GRACE_S + 1  # age past grace
    assert await feeder.tick() == 1  # a's slot released → submit b
    assert rec.calls == ["TV/a.mkv", "TV/b.mkv"]


@pytest.mark.asyncio
async def test_kick_triggers_prompt_tick_despite_long_interval(store):
    # #169: with a long interval the loop would normally sleep between ticks;
    # kick() must wake it so a freshly-enqueued manual job is fed within a tick,
    # not interval_s later.
    rec = SubmitRecorder()
    feeder = _feeder(store, FakeSubgen(), rec)
    feeder._interval_s = 60.0  # only a kick can produce a 2nd tick in-window
    feeder.start()
    try:
        await asyncio.sleep(0.05)  # first tick runs (pending empty → nothing)
        store.enqueue("TV/a.mkv", source="manual")
        feeder.kick()
        await asyncio.sleep(0.05)  # kick wakes the loop → feeds the job
        assert rec.calls == ["TV/a.mkv"]
    finally:
        await feeder.stop()


@pytest.mark.asyncio
async def test_stop_wakes_loop_without_waiting_out_interval(store):
    # stop() must not block on the (long) interval wait.
    feeder = _feeder(store, FakeSubgen(), SubmitRecorder())
    feeder._interval_s = 60.0
    feeder.start()
    await asyncio.sleep(0.05)
    await asyncio.wait_for(feeder.stop(), timeout=1.0)


# ── GPU-concurrency gate (arena-aware capacity) ─────────────────────────────


@pytest.mark.asyncio
async def test_feeder_holds_when_arena_consumes_the_only_slot(store):
    # concurrent_transcriptions=1 and arena has 1 in-flight → no GPU slots left.
    store.enqueue("TV/a.mkv", source="gaps")
    rec = SubmitRecorder()
    feeder = PendingQueueFeeder(
        store=store,
        subgen_provider=lambda: FakeSubgen(),
        submit_job=rec,
        target_depth_provider=lambda: 2,
        paused_provider=lambda: False,
        caps_provider=lambda: type("C", (), {"concurrent_transcriptions": 1})(),
        arena_inflight_provider=lambda: 1,
    )
    n = await feeder.tick()
    assert n == 0 and rec.calls == []


@pytest.mark.asyncio
async def test_feeder_unaffected_when_n_none(store):
    # concurrent_transcriptions=None disables the gate entirely.
    store.enqueue("TV/a.mkv", source="gaps")
    rec = SubmitRecorder()
    feeder = PendingQueueFeeder(
        store=store,
        subgen_provider=lambda: FakeSubgen(),
        submit_job=rec,
        target_depth_provider=lambda: 2,
        paused_provider=lambda: False,
        caps_provider=lambda: type("C", (), {"concurrent_transcriptions": None})(),
        arena_inflight_provider=lambda: 5,
    )
    n = await feeder.tick()
    assert n == 1 and len(rec.calls) == 1


@pytest.mark.asyncio
async def test_inflight_counted_in_capacity_gate_across_ticks(store):
    # Regression: with N=1 and target_depth=2, a 2nd job must NOT be submitted
    # when the 1st is still in _inflight (submitted but not yet surfaced in
    # subgen's /queue). The capacity gate must count self._inflight, not just
    # subgen's reported processing_count (which is still 0 while the job lags).
    #
    # N=1, target=2. Depth guard (target=2) would NOT stop the 2nd submission
    # (effective=0+1inflight=1 < 2). Only the capacity gate can stop it:
    # processing(0) + inflight(1) >= N(1) → blocked.
    store.enqueue("TV/a.mkv", source="gaps")
    store.enqueue("TV/b.mkv", source="gaps")
    # subgen always reports empty — simulating the /batch→/queue lag window
    empty_subgen = FakeSubgen()
    rec = SubmitRecorder()
    feeder = PendingQueueFeeder(
        store=store,
        subgen_provider=lambda: empty_subgen,
        submit_job=rec,
        target_depth_provider=lambda: 2,
        paused_provider=lambda: False,
        caps_provider=lambda: type("C", (), {"concurrent_transcriptions": 1})(),
        arena_inflight_provider=lambda: 0,
    )
    # First tick: slot free (processing=0, inflight=0) → submit one job
    n1 = await feeder.tick()
    assert n1 == 1, f"expected 1 submitted first tick, got {n1}"
    assert len(feeder._inflight) == 1
    # Second tick: subgen still reports empty but _inflight has 1 → gate blocks
    # processing(0) + inflight(1) = 1 >= N(1) → no more submissions
    n2 = await feeder.tick()
    assert n2 == 0, f"expected 0 submitted second tick (gate should block), got {n2}"
    assert len(rec.calls) == 1  # only the first job was ever submitted
