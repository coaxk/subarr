"""#291 Slice B — deep integrity check + VACUUM INTO backup."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path


def _migrated_db(tmp_path: Path) -> Path:
    from subarr.migrate import run_migrations

    db = tmp_path / "subarr.db"
    run_migrations(db)
    return db


# ─── deep_integrity_check ────────────────────────────────────────────────────


def test_deep_integrity_check_healthy(tmp_path):
    """A freshly-migrated db passes full integrity_check and returns (True, ['ok'])."""
    from subarr.db_integrity import deep_integrity_check

    db = _migrated_db(tmp_path)
    ok, findings = deep_integrity_check(db)
    assert ok is True
    assert findings == ["ok"]


def test_deep_integrity_check_missing_file(tmp_path):
    """A non-existent path: SQLite creates an empty db, which passes integrity_check.

    sqlite3.connect() on a missing path creates a new empty database rather
    than raising — the PRAGMA returns 'ok' on the empty file. This is expected
    behaviour: the absence of a file is not corruption; it is a missing-db
    situation caught by boot logic. deep_integrity_check is concerned with
    structural integrity, not presence.
    """
    from subarr.db_integrity import deep_integrity_check

    ok, findings = deep_integrity_check(tmp_path / "nonexistent.db")
    # SQLite creates an empty-but-valid db — integrity_check reports ok.
    assert ok is True
    assert findings == ["ok"]


def test_deep_integrity_check_not_a_database(tmp_path):
    """A file that is not a SQLite db returns (False, [message]) without raising."""
    from subarr.db_integrity import deep_integrity_check

    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not a database at all!!!")
    ok, findings = deep_integrity_check(bad)
    assert ok is False
    assert findings  # at least one finding / error string


# ─── vacuum_backup ───────────────────────────────────────────────────────────


def test_vacuum_backup_creates_valid_db(tmp_path):
    """vacuum_backup writes a file that is a valid, openable SQLite db."""
    from subarr.db_integrity import vacuum_backup

    db = _migrated_db(tmp_path)
    backups_dir = tmp_path / "backups"
    when = time.time()
    result = vacuum_backup(db, backups_dir, when=when)

    backup_path = Path(result["path"])
    assert backup_path.exists()
    assert result["size_bytes"] == backup_path.stat().st_size
    assert result["created_at"] == when
    assert result["pruned"] == []

    # Verify it is a valid, openable SQLite database
    conn = sqlite3.connect(str(backup_path))
    try:
        rows = conn.execute("SELECT 1 FROM schema_versions LIMIT 1").fetchall()
        assert isinstance(rows, list)
    finally:
        conn.close()


def test_vacuum_backup_creates_backups_dir(tmp_path):
    """backups_dir is created if it does not exist."""
    from subarr.db_integrity import vacuum_backup

    db = _migrated_db(tmp_path)
    backups_dir = tmp_path / "nested" / "backups"
    assert not backups_dir.exists()
    vacuum_backup(db, backups_dir, when=time.time())
    assert backups_dir.exists()


def test_vacuum_backup_prunes_oldest(tmp_path):
    """With keep=2 and 3 calls, the oldest backup is pruned."""
    from subarr.db_integrity import vacuum_backup

    db = _migrated_db(tmp_path)
    backups_dir = tmp_path / "backups"

    base_when = time.time()
    r1 = vacuum_backup(db, backups_dir, when=base_when, keep=2)
    r2 = vacuum_backup(db, backups_dir, when=base_when + 60, keep=2)
    r3 = vacuum_backup(db, backups_dir, when=base_when + 120, keep=2)

    # After the third call, only 2 files remain
    remaining = sorted(backups_dir.glob("*.db"))
    assert len(remaining) == 2

    # The oldest (r1) was pruned
    assert not Path(r1["path"]).exists()
    assert Path(r2["path"]).exists()
    assert Path(r3["path"]).exists()

    # r3's pruned list contains the name of r1
    assert len(r3["pruned"]) == 1
    assert Path(r1["path"]).name in r3["pruned"]


def test_vacuum_backup_result_path_uses_timestamp(tmp_path):
    """The backup filename encodes the timestamp from 'when'."""
    from subarr.db_integrity import vacuum_backup

    db = _migrated_db(tmp_path)
    backups_dir = tmp_path / "backups"
    when = 1700000000.0  # fixed timestamp for deterministic name
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(when))
    result = vacuum_backup(db, backups_dir, when=when)
    assert stamp in Path(result["path"]).name


def test_vacuum_backup_schema_versions_present(tmp_path):
    """The backup contains the same schema_versions rows as the source."""
    from subarr.db_integrity import vacuum_backup

    db = _migrated_db(tmp_path)
    src_rows = sqlite3.connect(str(db)).execute("SELECT version FROM schema_versions ORDER BY version").fetchall()

    backups_dir = tmp_path / "backups"
    result = vacuum_backup(db, backups_dir, when=time.time())

    bk_rows = (
        sqlite3.connect(result["path"]).execute("SELECT version FROM schema_versions ORDER BY version").fetchall()
    )
    assert bk_rows == src_rows
