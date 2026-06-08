"""#156 aftercare store + migration."""
from __future__ import annotations

import time

import pytest


def _migrated_db(tmp_path):
    from subarr.migrate import run_migrations
    db = tmp_path / "a.db"
    run_migrations(db)
    return db


def test_migration_creates_aftercare_table(tmp_path):
    import sqlite3
    db = _migrated_db(tmp_path)
    conn = sqlite3.connect(str(db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(aftercare_results)")}
    assert {"id", "canonical_path", "completed_at", "composite", "cue_count",
            "flagged", "readability_json", "signals_json", "source",
            "reviewed_at", "created_at"} <= cols
