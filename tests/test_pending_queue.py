"""#66/#116 slice 1: pending_queue store — the queue authority in front of
subgen. Covers enqueue+dedup, priority buckets, feeder ordering, status
transitions, removal, and reorder (promote/demote/move incl. cross-bucket).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from subarr.migrate import run_migrations
from subarr.pending_queue import (
    PendingQueueStore,
    STATUS_PENDING,
    STATUS_SUBMITTED,
    STATUS_DONE,
)


@pytest.fixture
def store(tmp_path: Path) -> PendingQueueStore:
    db = tmp_path / "subarr.db"
    run_migrations(db)  # includes 014_pending_queue
    return PendingQueueStore(db)


def _ids(jobs):
    return [j.canonical_path for j in jobs]


# ── enqueue + dedup ─────────────────────────────────────────────────


def test_enqueue_returns_job(store):
    j = store.enqueue("TV/A/s01e01.mkv", source="manual")
    assert j.status == STATUS_PENDING
    assert j.priority == 2  # manual bucket
    assert j.source == "manual"
    assert store.get(j.id) is not None


def test_enqueue_dedups_active_path(store):
    a = store.enqueue("TV/A/s01e01.mkv", source="gaps")
    b = store.enqueue("TV/A/s01e01.mkv", source="manual")  # same path, still active
    assert a.id == b.id  # returned existing, no duplicate
    assert len(store.list()) == 1


def test_enqueue_after_done_creates_new(store):
    a = store.enqueue("TV/A/s01e01.mkv", source="gaps")
    store.set_status(a.id, STATUS_DONE)
    b = store.enqueue("TV/A/s01e01.mkv", source="gaps")  # prior one is done → not active
    assert a.id != b.id
    assert len(store.list(status=STATUS_PENDING)) == 1


def test_submitted_still_dedups(store):
    a = store.enqueue("TV/A/s01e01.mkv", source="gaps")
    store.mark_submitted(a.id)
    b = store.enqueue("TV/A/s01e01.mkv", source="manual")  # in subgen now → still active
    assert a.id == b.id


# ── ordering / priority buckets ─────────────────────────────────────


def test_feeder_order_priority_then_position(store):
    store.enqueue("TV/back.mkv", source="backfill")  # prio 0
    store.enqueue("TV/gap1.mkv", source="gaps")  # prio 1
    store.enqueue("TV/gap2.mkv", source="gaps")  # prio 1
    store.enqueue("TV/man.mkv", source="manual")  # prio 2
    order = _ids(store.list(status=STATUS_PENDING))
    # manual first, then gaps in insert order, then backfill last
    assert order == ["TV/man.mkv", "TV/gap1.mkv", "TV/gap2.mkv", "TV/back.mkv"]


def test_next_pending_limit(store):
    store.enqueue("TV/back.mkv", source="backfill")
    store.enqueue("TV/man.mkv", source="manual")
    nxt = store.next_pending(1)
    assert len(nxt) == 1 and nxt[0].canonical_path == "TV/man.mkv"


# ── status transitions / removal ────────────────────────────────────


def test_mark_submitted_and_counts(store):
    a = store.enqueue("TV/a.mkv", source="gaps")
    store.enqueue("TV/b.mkv", source="gaps")
    store.mark_submitted(a.id)
    counts = store.count_by_status()
    assert counts.get(STATUS_PENDING) == 1
    assert counts.get(STATUS_SUBMITTED) == 1
    assert store.get(a.id).submitted_at is not None


def test_set_status_error(store):
    a = store.enqueue("TV/a.mkv", source="gaps")
    assert store.set_status(a.id, "error", error="boom") is True
    assert store.get(a.id).error == "boom"


def test_remove_and_clear(store):
    a = store.enqueue("TV/a.mkv", source="gaps")
    store.enqueue("TV/b.mkv", source="backfill")
    assert store.remove(a.id) is True
    assert store.remove("nope") is False
    assert store.clear(status=STATUS_PENDING) == 1
    assert store.list() == []


# ── reorder ─────────────────────────────────────────────────────────


def test_promote_to_top_of_bucket(store):
    store.enqueue("TV/g1.mkv", source="gaps")
    store.enqueue("TV/g2.mkv", source="gaps")
    c = store.enqueue("TV/g3.mkv", source="gaps")
    store.promote(c.id)
    order = _ids(store.list(status=STATUS_PENDING))
    assert order[0] == "TV/g3.mkv"


def test_demote_to_bottom_of_bucket(store):
    a = store.enqueue("TV/g1.mkv", source="gaps")
    store.enqueue("TV/g2.mkv", source="gaps")
    store.enqueue("TV/g3.mkv", source="gaps")
    store.demote(a.id)
    order = _ids(store.list(status=STATUS_PENDING))
    assert order[-1] == "TV/g1.mkv"


def test_promote_single_item_noop(store):
    a = store.enqueue("TV/only.mkv", source="gaps")
    store.promote(a.id)  # should not raise / corrupt
    assert _ids(store.list(status=STATUS_PENDING)) == ["TV/only.mkv"]


def test_move_before_within_bucket(store):
    store.enqueue("TV/g1.mkv", source="gaps")
    g2 = store.enqueue("TV/g2.mkv", source="gaps")
    g3 = store.enqueue("TV/g3.mkv", source="gaps")
    store.move(g3.id, before_id=g2.id)  # g3 now before g2
    order = _ids(store.list(status=STATUS_PENDING))
    assert order == ["TV/g1.mkv", "TV/g3.mkv", "TV/g2.mkv"]


def test_move_across_bucket_adopts_priority(store):
    man = store.enqueue("TV/man.mkv", source="manual")  # prio 2
    back = store.enqueue("TV/back.mkv", source="backfill")  # prio 0
    # drag the backfill job to sit right after the manual one → adopts prio 2
    store.move(back.id, after_id=man.id)
    moved = store.get(back.id)
    assert moved.priority == 2
    order = _ids(store.list(status=STATUS_PENDING))
    assert order == ["TV/man.mkv", "TV/back.mkv"]


def test_move_requires_target(store):
    a = store.enqueue("TV/a.mkv", source="gaps")
    with pytest.raises(ValueError):
        store.move(a.id)


def test_reorder_unknown_id_raises(store):
    with pytest.raises(KeyError):
        store.promote("nope")


# ── resolve_orphaned_submitted (#336) — see the dedicated tests appended below
# (the earlier #287 repend_orphaned_submitted was superseded and removed) ──────


def test_move_step_walks_to_top(store):
    """#336: repeated up steps walk a job to the top (the stale-client bug made
    arrows no-op / stop after a few clicks). move_step resolves the neighbour
    server-side, so each step actually advances."""
    jobs = [store.enqueue(f"TV/e{i}.mkv", source="gaps") for i in range(6)]
    bottom = jobs[-1].id
    for _ in range(5):
        store.move_step(bottom, up=True)
    assert [j.id for j in store.list(status="pending")][0] == bottom
    # At the top, another up is a no-op (no error, stays put).
    store.move_step(bottom, up=True)
    assert [j.id for j in store.list(status="pending")][0] == bottom


def test_move_step_crosses_priority_buckets(store):
    """A lower-priority backfill job can step up past a higher-priority gaps job,
    adopting its bucket (one step at a time)."""
    g = store.enqueue("TV/gap.mkv", source="gaps")  # priority 1
    b = store.enqueue("TV/backfill.mkv", source="backfill")  # priority 0 → below
    assert [j.id for j in store.list(status="pending")] == [g.id, b.id]
    store.move_step(b.id, up=True)
    assert [j.id for j in store.list(status="pending")][0] == b.id


def test_move_step_down_walks_to_bottom_then_noop(store):
    jobs = [store.enqueue(f"TV/d{i}.mkv", source="gaps") for i in range(4)]
    top = jobs[0].id
    for _ in range(3):
        store.move_step(top, up=False)
    assert [j.id for j in store.list(status="pending")][-1] == top
    store.move_step(top, up=False)  # at bottom → no-op
    assert [j.id for j in store.list(status="pending")][-1] == top


def test_resolve_orphaned_submitted_removes_past_grace(store):
    """#336: a SUBMITTED row subgen no longer reports, aged past the grace, is
    REMOVED (not re-pended) so already-done files stop churning the feeder."""
    import time

    job = store.enqueue("TV/gone.mkv", source="gaps")
    store.mark_submitted(job.id)
    store._conn.execute("UPDATE pending_queue SET submitted_at = ? WHERE id = ?", (time.time() - 120, job.id))
    removed = store.resolve_orphaned_submitted(set(), older_than_s=60.0)
    assert removed == 1
    assert store.get(job.id) is None


def test_resolve_orphaned_submitted_keeps_within_grace(store):
    """A just-submitted row (within grace) is left alone — subgen may not have
    surfaced it in /queue yet."""
    job = store.enqueue("TV/fresh.mkv", source="gaps")
    store.mark_submitted(job.id)  # submitted_at = now
    removed = store.resolve_orphaned_submitted(set(), older_than_s=60.0)
    assert removed == 0
    assert store.get(job.id).status == STATUS_SUBMITTED


def test_resolve_orphaned_submitted_keeps_if_in_subgen(store):
    """A SUBMITTED row whose path IS in subgen's live queue is kept even past
    grace — it's actively processing, not orphaned."""
    import time

    from subarr.paths import canonical_to_subgen_batch

    job = store.enqueue("TV/active.mkv", source="gaps")
    store.mark_submitted(job.id)
    store._conn.execute("UPDATE pending_queue SET submitted_at = ? WHERE id = ?", (time.time() - 120, job.id))
    live = {canonical_to_subgen_batch("TV/active.mkv")}
    removed = store.resolve_orphaned_submitted(live, older_than_s=60.0)
    assert removed == 0
    assert store.get(job.id).status == STATUS_SUBMITTED


def test_enqueue_persists_radarr_movie_id(store):
    # #368: a movie job carries its Radarr id so completion_watcher can upload
    # the finished .srt to the owning Bazarr. It must round-trip through the DB.
    j = store.enqueue("Movies/Dune (2021)/Dune.mkv", source="auto", radarr_movie_id=909)
    assert j.radarr_movie_id == 909
    fetched = store.get(j.id)
    assert fetched is not None and fetched.radarr_movie_id == 909
    assert fetched.to_dict()["radarr_movie_id"] == 909


def test_enqueue_radarr_movie_id_defaults_none(store):
    # episodes / path-only jobs leave it NULL (byte-identical to before #368)
    j = store.enqueue("TV/A/s01e01.mkv", source="manual")
    assert j.radarr_movie_id is None
    assert store.get(j.id).radarr_movie_id is None
