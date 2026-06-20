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


# ── repend_orphaned_submitted (#287) ────────────────────────────────


def test_repend_orphaned_submitted_basic(store):
    """Job whose sg_path is NOT in live_subgen_paths gets re-pended; job whose
    sg_path IS still in live set stays SUBMITTED. Returns count of re-pended."""
    from subarr.paths import canonical_to_subgen_batch

    j1 = store.enqueue("TV/S01/e01.mkv", source="gaps")
    j2 = store.enqueue("TV/S01/e02.mkv", source="gaps")
    store.mark_submitted(j1.id)
    store.mark_submitted(j2.id)

    sg1 = canonical_to_subgen_batch("TV/S01/e01.mkv")
    # j1 is still in subgen; j2 is orphaned (not in the live set)
    n = store.repend_orphaned_submitted({sg1})
    assert n == 1

    j1_after = store.get(j1.id)
    j2_after = store.get(j2.id)
    assert j1_after.status == STATUS_SUBMITTED  # kept
    assert j2_after.status == STATUS_PENDING  # re-pended
    assert j2_after.submitted_at is None  # cleared


def test_repend_orphaned_submitted_all_live(store):
    """When every SUBMITTED job is in the live set, nothing is re-pended."""
    from subarr.paths import canonical_to_subgen_batch

    j = store.enqueue("TV/a.mkv", source="gaps")
    store.mark_submitted(j.id)
    sg = canonical_to_subgen_batch("TV/a.mkv")
    n = store.repend_orphaned_submitted({sg})
    assert n == 0
    assert store.get(j.id).status == STATUS_SUBMITTED


def test_repend_orphaned_submitted_empty_live(store):
    """Empty live set means every SUBMITTED row is orphaned and re-pended."""
    j1 = store.enqueue("TV/a.mkv", source="gaps")
    j2 = store.enqueue("TV/b.mkv", source="gaps")
    store.mark_submitted(j1.id)
    store.mark_submitted(j2.id)
    n = store.repend_orphaned_submitted(set())
    assert n == 2
    for jid in (j1.id, j2.id):
        j = store.get(jid)
        assert j.status == STATUS_PENDING
        assert j.submitted_at is None


def test_repend_orphaned_submitted_skips_pending_and_done(store):
    """Only SUBMITTED rows are candidates; PENDING and DONE are untouched."""

    p = store.enqueue("TV/pending.mkv", source="gaps")
    s = store.enqueue("TV/submitted.mkv", source="gaps")
    store.mark_submitted(s.id)
    # mark the first one done
    store.set_status(p.id, STATUS_DONE)
    # Neither the done nor pending path is in the live set
    n = store.repend_orphaned_submitted(set())
    assert n == 1  # only the submitted one
    assert store.get(p.id).status == STATUS_DONE
    # PENDING job was never submitted so it would not be touched regardless,
    # but here it's STATUS_DONE — confirm unchanged
    assert store.get(s.id).status == STATUS_PENDING
