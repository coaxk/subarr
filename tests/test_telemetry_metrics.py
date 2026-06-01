"""Real telemetry metrics: walks_per_day_30d (scan count / 30) and
error_counts_30d (anonymous exception-class tally), replacing the old
hardcoded 0.0 / {}."""

from __future__ import annotations

import time

import pytest


def _migrated_db(tmp_path):
    from subarr.migrate import run_migrations
    db = tmp_path / "t.db"
    run_migrations(db)
    return db


def test_scan_store_count_since(tmp_path):
    from subarr.scan_store import ScanStore
    s = ScanStore(_migrated_db(tmp_path))
    now = time.time()
    # 3 recent scans
    for _ in range(3):
        s.create(["TV/X"], reverse=False)
    assert s.count_since(now - 86400) == 3
    # window in the future excludes them
    assert s.count_since(now + 86400) == 0


def test_error_store_record_and_counts(tmp_path):
    from subarr.error_store import ErrorStore
    es = ErrorStore(_migrated_db(tmp_path))
    now = time.time()
    es.record("SubgenUnavailable", when=now - 10)
    es.record("SubgenUnavailable", when=now - 5)
    es.record("ConnectError", when=now - 1)
    es.record("OldError", when=now - 40 * 86400)  # outside 30d
    counts = es.counts_since(now - 30 * 86400)
    assert counts == {"SubgenUnavailable": 2, "ConnectError": 1}


def test_error_store_stores_only_class_name(tmp_path):
    """Anonymity: keys must be bare class identifiers (no messages/paths)."""
    import re
    from subarr.error_store import ErrorStore
    es = ErrorStore(_migrated_db(tmp_path))
    es.record("ValueError")
    es.record("x" * 200)  # over-long is truncated, never a message
    for k in es.counts_since(0):
        assert len(k) <= 80
    assert "ValueError" in es.counts_since(0)


def test_error_store_record_never_raises(tmp_path):
    """record() on a closed/broken store must not propagate."""
    from subarr.error_store import ErrorStore
    es = ErrorStore(_migrated_db(tmp_path))
    es.close()
    es.record("SomethingError")  # should swallow, not raise
