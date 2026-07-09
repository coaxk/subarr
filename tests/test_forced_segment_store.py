"""#364 slice 1 — scan-result cache. Migration 028 applies cleanly; the store
records scanned/none/bailed keyed on (canonical_path, mtime, size), and a stale
mtime/size is a miss (re-scan)."""

from __future__ import annotations

import sqlite3


from subarr.migrate import run_migrations
from subarr.forced_segment_store import ForcedSegmentScanStore


def test_migration_028_creates_table(tmp_path):
    db = tmp_path / "subarr.db"
    run_migrations(db)  # 001..028
    conn = sqlite3.connect(str(db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(forced_segment_scans)")}
    conn.close()
    assert {"canonical_path", "mtime", "size", "status", "n_spans", "total_ms", "scanned_at"} <= cols


def test_migration_028_idempotent(tmp_path):
    db = tmp_path / "subarr.db"
    run_migrations(db)
    assert run_migrations(db) == []


def _store(tmp_path):
    db = tmp_path / "subarr.db"
    run_migrations(db)
    return ForcedSegmentScanStore(db)


def test_hit_only_on_matching_mtime_and_size(tmp_path):
    s = _store(tmp_path)
    s.upsert(canonical_path="TV/S/ep.mkv", mtime=100.0, size=42, status="scanned", n_spans=2, total_ms=14000)
    hit = s.get("TV/S/ep.mkv", mtime=100.0, size=42)
    assert hit is not None and hit.status == "scanned" and hit.n_spans == 2 and hit.total_ms == 14000
    assert s.get("TV/S/ep.mkv", mtime=200.0, size=42) is None  # stale mtime -> miss
    assert s.get("TV/S/ep.mkv", mtime=100.0, size=99) is None  # changed size -> miss
    assert s.get("Other/x.mkv", mtime=1.0, size=1) is None


def test_upsert_replaces_prior_verdict(tmp_path):
    s = _store(tmp_path)
    s.upsert(canonical_path="TV/S/ep.mkv", mtime=1.0, size=1, status="none", n_spans=0, total_ms=0)
    s.upsert(canonical_path="TV/S/ep.mkv", mtime=2.0, size=2, status="bailed", n_spans=0, total_ms=0)
    hit = s.get("TV/S/ep.mkv", mtime=2.0, size=2)
    assert hit.status == "bailed"


def test_error_and_vad_unavailable_rows_are_rescannable(tmp_path):
    # Transient verdicts (subgen down / VAD model not pulled yet) must NOT count
    # as a cache hit, so the next walk re-scans without the file having to change.
    # Terminal verdicts settle the (path, mtime, size) and DO hit.
    s = _store(tmp_path)
    s.upsert(canonical_path="TV/S/ep.mkv", mtime=100.0, size=42, status="error", n_spans=0, total_ms=0)
    assert s.get("TV/S/ep.mkv", mtime=100.0, size=42) is None  # error -> re-scannable
    s.upsert(
        canonical_path="TV/S/ep.mkv", mtime=100.0, size=42, status="vad-unavailable", n_spans=0, total_ms=0
    )
    assert s.get("TV/S/ep.mkv", mtime=100.0, size=42) is None  # vad-unavailable -> re-scannable
    # A later successful scan settles it: now it hits.
    s.upsert(canonical_path="TV/S/ep.mkv", mtime=100.0, size=42, status="scanned", n_spans=1, total_ms=3000)
    hit = s.get("TV/S/ep.mkv", mtime=100.0, size=42)
    assert hit is not None and hit.status == "scanned"
