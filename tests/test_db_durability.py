"""#291 durability hardening:
- migrate.py tolerates a concurrent-boot schema_versions collision (dev
  --reload) instead of crashing.
- data_persistence.mount_fstype_for resolves a path to its mount fstype so
  WAL-over-network-FS can be warned.
"""

from __future__ import annotations

import sqlite3

from subarr.data_persistence import mount_fstype_for
from subarr.migrate import MigrationRunner


# ── migrate concurrent-boot collision ────────────────────────────────────────


def test_apply_one_benign_on_concurrent_version_collision(tmp_path):
    """Two processes booting together both read applied-versions before either
    commits; the 2nd's version INSERT collides. It must roll back its duplicate
    work and continue — no crash, no corruption."""
    db = tmp_path / "m.db"
    mig_dir = tmp_path / "migs"
    mig_dir.mkdir()
    (mig_dir / "001_x.sql").write_text("CREATE TABLE IF NOT EXISTS t (id INTEGER);")
    runner = MigrationRunner(db, mig_dir)

    conn = sqlite3.connect(str(db), isolation_level=None)
    conn.execute("PRAGMA busy_timeout=5000")
    runner._ensure_version_table(conn)
    # Simulate a concurrent migrator that recorded v1 between our read and apply.
    conn.execute("INSERT INTO schema_versions (version, name, applied_at) VALUES (1, '001_x', 0)")
    mig = runner.discover()[0]

    # Must NOT raise despite the duplicate version INSERT inside _apply_one.
    runner._apply_one(conn, mig)

    # State stays consistent: exactly one v1 row.
    assert conn.execute("SELECT count(*) FROM schema_versions WHERE version = 1").fetchone()[0] == 1
    conn.close()


def test_run_is_idempotent_and_records_busy_timeout_path(tmp_path):
    """A normal run applies, and a second run is a clean no-op (regression
    guard around the busy_timeout/PRAGMA additions)."""
    db = tmp_path / "m.db"
    mig_dir = tmp_path / "migs"
    mig_dir.mkdir()
    (mig_dir / "001_x.sql").write_text("CREATE TABLE IF NOT EXISTS t (id INTEGER);")
    runner = MigrationRunner(db, mig_dir)

    first = runner.run()
    assert [m.version for m in first] == [1]
    second = runner.run()
    assert second == []  # already up to date


# ── mount fstype parser ──────────────────────────────────────────────────────

_EXT4 = "24 30 0:22 / / rw,relatime shared:1 - ext4 /dev/root rw"
_NFS = "36 24 0:33 / /data rw,relatime shared:2 - nfs4 server:/export rw"
_CIFS = "40 24 0:40 / /data rw - cifs //nas/share rw"


def test_mount_fstype_local_disk():
    assert mount_fstype_for("/data", _EXT4 + "\n" + "37 24 0:35 / /data - ext4 /dev/sdb1 rw") == "ext4"


def test_mount_fstype_nfs():
    assert mount_fstype_for("/data", _EXT4 + "\n" + _NFS) == "nfs4"


def test_mount_fstype_cifs():
    assert mount_fstype_for("/data", _EXT4 + "\n" + _CIFS) == "cifs"


def test_mount_fstype_longest_prefix_wins():
    # /data on nfs nested under / on ext4 → the deeper mount wins.
    mi = "\n".join([_EXT4, _NFS])
    assert mount_fstype_for("/data/subarr/subarr.db", mi) == "nfs4"


def test_mount_fstype_no_match_returns_none():
    assert mount_fstype_for("/data", "garbage line with no separator") is None
