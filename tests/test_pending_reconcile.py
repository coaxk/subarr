"""#469: one-shot cleanup for pending rows whose coverage is already satisfied.

Companion to #468. That stops NEW rows getting stuck; this clears the ones
already there. @AztecGuyGDL had 110 of them.

The dangerous direction here is deleting queued work the user still wants, so
every judgement call leans toward KEEPING a row.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from subarr.migrate import run_migrations
from subarr.pending_queue import PendingQueueStore
from subarr.pending_reconcile import (
    is_full_english_sidecar,
    reconcile_pending,
)


@pytest.fixture
def store(tmp_path: Path) -> PendingQueueStore:
    db = tmp_path / "subarr.db"
    run_migrations(db)
    return PendingQueueStore(db)


# ── what counts as a satisfying sidecar ────────────────────────────────────
def test_a_plain_english_srt_satisfies():
    assert is_full_english_sidecar("ep.en.srt", "ep") is True
    assert is_full_english_sidecar("ep.eng.srt", "ep") is True


def test_an_engine_suffixed_srt_still_satisfies():
    # subsyncarr writes several variants per file
    assert is_full_english_sidecar("ep.en.alass.srt", "ep") is True


def test_a_FORCED_sidecar_does_NOT_satisfy():
    """The trap in the reporter's own example.

    Their file had `ep.eng.srt` AND `ep.forced.en.srt`. A forced sidecar covers
    foreign dialogue in an otherwise-English film, not the whole show, so
    treating it as coverage would delete a pending row for a genuine gap. This
    is the #79 defect class, one layer over.
    """
    assert is_full_english_sidecar("ep.forced.en.srt", "ep") is False
    assert is_full_english_sidecar("ep.en.forced.srt", "ep") is False


def test_image_sidecars_never_satisfy():
    """#458: a bitmap is not text. .idx/.sub/.pgs must never count."""
    for name in ("ep.en.idx", "ep.en.sub", "ep.en.pgs"):
        assert is_full_english_sidecar(name, "ep") is False


def test_a_non_english_srt_does_not_satisfy():
    assert is_full_english_sidecar("ep.fr.srt", "ep") is False
    assert is_full_english_sidecar("ep.srt", "ep") is False


def test_a_sidecar_for_a_different_file_does_not_satisfy():
    assert is_full_english_sidecar("other.en.srt", "ep") is False


# ── the reconcile pass ─────────────────────────────────────────────────────
def _siblings(mapping):
    """Injected directory lister: canonical_path -> sibling filenames."""
    return lambda canonical: mapping.get(canonical, [])


def test_dry_run_reports_but_deletes_nothing(store):
    job = store.enqueue("TV/Show/ep.mkv", source="gaps")
    store.mark_submitted(job.id)

    rep = reconcile_pending(
        store,
        list_siblings=_siblings({"TV/Show/ep.mkv": ["ep.mkv", "ep.en.srt"]}),
        dry_run=True,
    )
    assert rep.satisfied == ["TV/Show/ep.mkv"]
    assert rep.resolved == 0
    assert store.get(job.id) is not None


def test_apply_removes_the_satisfied_row(store):
    job = store.enqueue("TV/Show/ep.mkv", source="gaps")
    store.mark_submitted(job.id)

    rep = reconcile_pending(
        store,
        list_siblings=_siblings({"TV/Show/ep.mkv": ["ep.mkv", "ep.en.srt"]}),
    )
    assert rep.resolved == 1
    assert store.get(job.id) is None


def test_a_row_with_only_a_forced_sidecar_is_KEPT(store):
    job = store.enqueue("TV/Show/ep.mkv", source="gaps")
    store.mark_submitted(job.id)

    rep = reconcile_pending(
        store,
        list_siblings=_siblings({"TV/Show/ep.mkv": ["ep.mkv", "ep.forced.en.srt"]}),
    )
    assert rep.resolved == 0
    assert store.get(job.id) is not None, "forced-only is a real gap, not coverage"


def test_a_bypass_skip_row_is_NEVER_resolved(store):
    """The constraint that would silently undo #466/#467.

    A bypass_skip job is submitted deliberately against subgen's judgement, for
    a file whose only English subtitle is an image track. Such a file can even
    have an .en.idx sibling. Dropping it would delete exactly the work the user
    explicitly asked for.
    """
    job = store.enqueue("TV/Show/ep.mkv", source="gaps", bypass_skip=True)
    store.mark_submitted(job.id)

    rep = reconcile_pending(
        store,
        list_siblings=_siblings({"TV/Show/ep.mkv": ["ep.mkv", "ep.en.srt"]}),
    )
    assert rep.resolved == 0
    assert store.get(job.id) is not None
    assert "TV/Show/ep.mkv" not in rep.satisfied


def test_pending_rows_are_left_alone(store):
    """Only SUBMITTED. A PENDING row has not been sent to subgen, so it is
    still queued work the user is waiting on."""
    job = store.enqueue("TV/Show/ep.mkv", source="gaps")
    rep = reconcile_pending(
        store,
        list_siblings=_siblings({"TV/Show/ep.mkv": ["ep.mkv", "ep.en.srt"]}),
    )
    assert rep.resolved == 0
    assert store.get(job.id) is not None


def test_an_unreadable_directory_keeps_the_row(store):
    """Fails SAFE.

    If the media mount is down every listing comes back empty, which reads as
    "no sidecar" and therefore "not satisfied", so rows are KEPT. That is the
    correct direction: the opposite would delete the whole queue during an
    outage.
    """
    job = store.enqueue("TV/Show/ep.mkv", source="gaps")
    store.mark_submitted(job.id)

    def _boom(canonical):
        raise OSError("mount is down")

    rep = reconcile_pending(store, list_siblings=_boom)
    assert rep.resolved == 0
    assert store.get(job.id) is not None


def test_empty_queue_is_a_clean_no_op(store):
    rep = reconcile_pending(store, list_siblings=_siblings({}))
    assert rep.resolved == 0
    assert rep.satisfied == []


# ── the endpoints ──────────────────────────────────────────────────────────
DRY = "/api/admin/db/pending-satisfied"
APPLY = "/api/admin/db/pending-satisfied/reconcile"


def test_dry_run_endpoint_lists_but_does_not_resolve(app_with_stub, media_root):
    c = app_with_stub
    q = c.app.state.pending_queue
    f = media_root / "TV" / "Show" / "ep.mkv"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"x")
    (f.parent / "ep.en.srt").write_text("1\n", encoding="utf-8")

    job = q.enqueue("TV/Show/ep.mkv", source="gaps")
    q.mark_submitted(job.id)

    body = c.get(DRY).json()
    assert "TV/Show/ep.mkv" in body["satisfied"]
    assert body["resolved"] == 0
    assert q.get(job.id) is not None


def test_apply_endpoint_resolves_it(app_with_stub, media_root):
    c = app_with_stub
    q = c.app.state.pending_queue
    f = media_root / "TV" / "Show" / "ep.mkv"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"x")
    (f.parent / "ep.en.srt").write_text("1\n", encoding="utf-8")

    job = q.enqueue("TV/Show/ep.mkv", source="gaps")
    q.mark_submitted(job.id)

    body = c.post(APPLY).json()
    assert body["resolved"] == 1
    assert q.get(job.id) is None


def test_apply_endpoint_spares_a_bypass_skip_row(app_with_stub, media_root):
    """End-to-end guard on the constraint that would undo #466/#467."""
    c = app_with_stub
    q = c.app.state.pending_queue
    f = media_root / "TV" / "Show" / "ep.mkv"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"x")
    (f.parent / "ep.en.srt").write_text("1\n", encoding="utf-8")

    job = q.enqueue("TV/Show/ep.mkv", source="gaps", bypass_skip=True)
    q.mark_submitted(job.id)

    body = c.post(APPLY).json()
    assert body["resolved"] == 0
    assert body["skipped_bypass"] == 1
    assert q.get(job.id) is not None
