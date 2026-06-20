"""#287 NIT: provenance.record() must dedup OPEN (pending) rows so a re-search
of an in-flight path doesn't create a second ledger row — two open rows both
poll to completion and fire _run_aftercare twice for one transcription.

Also: mark_completed stamps the FIRST completion only (idempotent re-poll).
"""

from __future__ import annotations


def _store(tmp_path):
    from subarr.migrate import run_migrations
    from subarr.provenance import ProvenanceStore

    db = tmp_path / "p.db"
    run_migrations(db)
    return ProvenanceStore(db)


def test_record_dedups_open_pending_row(tmp_path):
    store = _store(tmp_path)
    first = store.record(canonical_path="/m/A.mkv", scan_id="s1")
    # re-search the same still-in-flight path → reuse the open row, no insert
    second = store.record(canonical_path="/m/A.mkv", scan_id="s2")
    assert second == first
    assert len(store.pending()) == 1  # exactly one open ledger row


def test_record_inserts_new_row_after_completion(tmp_path):
    store = _store(tmp_path)
    first = store.record(canonical_path="/m/A.mkv", scan_id="s1")
    store.mark_completed(first)
    # path completed; a fresh transcription of the same path is a NEW row
    second = store.record(canonical_path="/m/A.mkv", scan_id="s2")
    assert second != first
    assert len(store.pending()) == 1  # only the new one is open


def test_record_distinct_paths_independent(tmp_path):
    store = _store(tmp_path)
    a = store.record(canonical_path="/m/A.mkv", scan_id="s1")
    b = store.record(canonical_path="/m/B.mkv", scan_id="s1")
    assert a != b
    assert len(store.pending()) == 2


def test_mark_completed_keeps_first_stamp(tmp_path):
    store = _store(tmp_path)
    lid = store.record(canonical_path="/m/A.mkv", scan_id="s1")
    store.mark_completed(lid, when=1000.0)
    store.mark_completed(lid, when=2000.0)  # a later re-poll must NOT overwrite
    row = store.query_by_path("/m/A.mkv")[0]
    assert row.completed_at == 1000.0
