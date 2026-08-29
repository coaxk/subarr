"""#468: propagate subgen's skip outcome instead of waiting out the orphan sweep.

Reported by @AztecGuyGDL on #453 with 110 affected rows in one scan.

subarr already receives the answer and throws it away. POST /batch replies
`{"walked": 1, "queued": 0, "skipped": 1}` immediately and classify_batch_outcome
maps it to SKIPPED within milliseconds, but the feeder's submit_job callable
returns None, so the outcome never reaches the pending row. The row then sits
SUBMITTED until resolve_orphaned_submitted clears it after ORPHAN_GRACE_S (60s).

The GPU cost is NOT the orphan sweep, which clears every eligible row in one
call. It is the depth slot: a submitted job reserves one until it surfaces in
subgen's queue or INFLIGHT_GRACE_S (30s) expires, and a skipped file never
surfaces. So both the row AND the slot have to be released.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from subarr.migrate import run_migrations
from subarr.pending_queue import STATUS_SUBMITTED, PendingQueueStore


@pytest.fixture
def store(tmp_path: Path) -> PendingQueueStore:
    db = tmp_path / "subarr.db"
    run_migrations(db)
    return PendingQueueStore(db)


# ── the store must be able to resolve one path on demand ────────────────────
def test_resolve_skipped_removes_the_submitted_row(store):
    job = store.enqueue("TV/Show/ep.mkv", source="gaps")
    store.mark_submitted(job.id)
    assert store.get(job.id).status == STATUS_SUBMITTED

    n = store.resolve_skipped("TV/Show/ep.mkv")
    assert n == 1
    assert store.get(job.id) is None, "a skipped row has done its job and is dropped"


def test_resolve_skipped_leaves_pending_rows_alone(store):
    """Only SUBMITTED rows are resolved.

    A PENDING row for the same path has not been sent to subgen yet, so the
    skip verdict says nothing about it. Dropping it would silently discard work
    the user is still waiting on.
    """
    job = store.enqueue("TV/Show/ep.mkv", source="gaps")
    assert store.resolve_skipped("TV/Show/ep.mkv") == 0
    assert store.get(job.id) is not None


def test_resolve_skipped_does_not_touch_other_paths(store):
    a = store.enqueue("TV/Show/a.mkv", source="gaps")
    b = store.enqueue("TV/Show/b.mkv", source="gaps")
    store.mark_submitted(a.id)
    store.mark_submitted(b.id)

    store.resolve_skipped("TV/Show/a.mkv")
    assert store.get(a.id) is None
    assert store.get(b.id) is not None


def test_resolve_skipped_on_an_unknown_path_is_a_no_op(store):
    assert store.resolve_skipped("TV/Nothing/here.mkv") == 0


# ── the runner must report the outcome ──────────────────────────────────────
def test_scan_runner_accepts_an_outcome_recorder():
    import inspect

    from subarr.scan_runner import ScanRunner

    assert "outcome_recorder" in inspect.signature(ScanRunner.__init__).parameters


def test_outcome_recorder_defaults_to_a_no_op():
    """It must never be required, and must never raise into the scan path.

    Same contract as error_recorder beside it: a telemetry-ish hook that cannot
    be allowed to break a transcription.
    """
    from subarr.scan_runner import ScanRunner

    r = ScanRunner(subgen=None, store=None)
    r._outcome_recorder("TV/x.mkv", "skipped", "sub exists")  # must not raise


# ── the feeder must release the slot, not just drop the row ────────────────
def test_feeder_exposes_inflight_release():
    import inspect

    from subarr.pending_feeder import PendingQueueFeeder

    assert hasattr(PendingQueueFeeder, "release_inflight")
    assert "canonical_path" in inspect.signature(PendingQueueFeeder.release_inflight).parameters


def test_releasing_inflight_frees_the_depth_slot():
    """The whole point of #468.

    Without this the row is gone but the slot is still reserved for the full
    30s grace, so the feeder still cannot refill it and the GPU still idles.
    """
    from subarr.paths import canonical_to_subgen_batch
    from subarr.pending_feeder import PendingQueueFeeder

    f = PendingQueueFeeder.__new__(PendingQueueFeeder)
    sg = canonical_to_subgen_batch("TV/Show/ep.mkv")
    f._inflight = {sg: 12345.0}

    f.release_inflight("TV/Show/ep.mkv")
    assert sg not in f._inflight


def test_releasing_an_unknown_path_is_harmless():
    from subarr.pending_feeder import PendingQueueFeeder

    f = PendingQueueFeeder.__new__(PendingQueueFeeder)
    f._inflight = {}
    f.release_inflight("TV/Nothing/here.mkv")  # must not raise
    assert f._inflight == {}


# ── the link that matters: does the runner actually CALL it? ────────────────
# Every piece above can be correct while the recorder is never invoked, which
# would leave the whole fix inert and every unit test still green.


@pytest.mark.asyncio
async def test_runner_reports_a_skip_to_the_recorder(tmp_path):
    import asyncio

    from subarr.scan_runner import PATH_STATUS_SKIPPED, ScanRunner
    from subarr.scan_store import ScanStore

    seen = []

    class _Subgen:
        async def batch(self, directory, **kw):
            # exactly what subgen returns for an already-covered file
            return 200, {"walked": 1, "queued": 0, "skipped": 1, "already_in_queue": 0}

    db = tmp_path / "scans.db"
    run_migrations(db)
    store = ScanStore(db)
    runner = ScanRunner(
        subgen=_Subgen(),
        store=store,
        outcome_recorder=lambda path, status, detail: seen.append((path, status, detail)),
    )
    scan = store.create(["TV/Show/ep.mkv"], reverse=False)
    runner.start(scan)
    for _ in range(200):  # let the scan task run
        await asyncio.sleep(0.01)
        if seen:
            break

    assert seen, "runner never reported the outcome — the fix would be inert"
    path, status, detail = seen[0]
    assert path == "TV/Show/ep.mkv"
    assert status == PATH_STATUS_SKIPPED
    assert "skipped" in (detail or "").lower()


@pytest.mark.asyncio
async def test_a_queued_file_is_not_reported_as_skipped(tmp_path):
    """The negative case. If OK also reported SKIPPED we would delete pending
    rows for files subgen actually accepted, which is far worse than the bug."""
    import asyncio

    from subarr.scan_runner import PATH_STATUS_SKIPPED, ScanRunner
    from subarr.scan_store import ScanStore

    seen = []

    class _Subgen:
        async def batch(self, directory, **kw):
            return 200, {"walked": 1, "queued": 1, "skipped": 0, "already_in_queue": 0}

    db = tmp_path / "scans.db"
    run_migrations(db)
    store = ScanStore(db)
    runner = ScanRunner(
        subgen=_Subgen(),
        store=store,
        outcome_recorder=lambda path, status, detail: seen.append((path, status, detail)),
    )
    scan = store.create(["TV/Show/ep.mkv"], reverse=False)
    runner.start(scan)
    for _ in range(200):
        await asyncio.sleep(0.01)
        if seen:
            break

    assert seen, "recorder should still fire, just not with SKIPPED"
    assert seen[0][1] != PATH_STATUS_SKIPPED
