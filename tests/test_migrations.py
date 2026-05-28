"""Tests for the migration runner.

Covers:
  - Fresh DB → all migrations apply, schema_versions populated
  - Already-up-to-date DB → no-op, no double-apply
  - Failing migration → rolls back, does NOT insert version row, raises
  - Discovery skips files without a numeric prefix
  - Adding a new migration file → applies on next run, leaves older alone
  - Public applied_versions() reflects on-disk state
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from subarr.migrate import MigrationRunner, run_migrations


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def tmp_migrations(tmp_path: Path) -> Path:
    """Empty migrations dir for the test to populate."""
    d = tmp_path / "migrations"
    d.mkdir()
    return d


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "subarr.db"


def write_migration(dir_: Path, version: int, name: str, sql: str) -> Path:
    fn = dir_ / f"{version:03d}_{name}.sql"
    fn.write_text(sql, encoding="utf-8")
    return fn


# ─── Tests ──────────────────────────────────────────────────────────


def test_fresh_db_applies_all_migrations(db_path: Path, tmp_migrations: Path):
    write_migration(tmp_migrations, 1, "baseline",
                    "CREATE TABLE foo (id INTEGER PRIMARY KEY, name TEXT);")
    write_migration(tmp_migrations, 2, "add_bar",
                    "CREATE TABLE bar (id INTEGER PRIMARY KEY);")

    applied = MigrationRunner(db_path, tmp_migrations).run()

    assert [m.version for m in applied] == [1, 2]
    assert [m.name for m in applied] == ["001_baseline", "002_add_bar"]

    # Verify tables exist
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = [r[0] for r in rows]
    assert "foo" in names
    assert "bar" in names
    assert "schema_versions" in names

    # schema_versions populated
    versions = conn.execute(
        "SELECT version, name FROM schema_versions ORDER BY version"
    ).fetchall()
    assert versions == [(1, "001_baseline"), (2, "002_add_bar")]
    conn.close()


def test_already_up_to_date_is_noop(db_path: Path, tmp_migrations: Path):
    write_migration(tmp_migrations, 1, "baseline",
                    "CREATE TABLE foo (id INTEGER PRIMARY KEY);")
    runner = MigrationRunner(db_path, tmp_migrations)
    first = runner.run()
    second = runner.run()
    third = runner.run()
    assert len(first) == 1
    assert second == []
    assert third == []


def test_failing_migration_rolls_back(db_path: Path, tmp_migrations: Path):
    write_migration(tmp_migrations, 1, "good",
                    "CREATE TABLE good (id INTEGER PRIMARY KEY);")
    write_migration(tmp_migrations, 2, "broken",
                    "CREATE TABLE broken (id INTEGER PRIMARY KEY); "
                    "THIS IS NOT VALID SQL;")

    runner = MigrationRunner(db_path, tmp_migrations)
    with pytest.raises(sqlite3.OperationalError):
        runner.run()

    # Good migration committed. Broken did not — version row absent.
    conn = sqlite3.connect(str(db_path))
    versions = conn.execute(
        "SELECT version FROM schema_versions ORDER BY version"
    ).fetchall()
    assert versions == [(1,)]
    conn.close()

    # Fixing the broken file + re-running should NOT re-apply good.
    write_migration(tmp_migrations, 2, "broken",
                    "CREATE TABLE fixed (id INTEGER PRIMARY KEY);")
    applied = runner.run()
    assert [m.version for m in applied] == [2]


def test_incremental_apply_after_new_migration(db_path: Path, tmp_migrations: Path):
    write_migration(tmp_migrations, 1, "v1", "CREATE TABLE t1 (id INTEGER PRIMARY KEY);")
    runner = MigrationRunner(db_path, tmp_migrations)
    runner.run()

    write_migration(tmp_migrations, 2, "v2", "CREATE TABLE t2 (id INTEGER PRIMARY KEY);")
    second = runner.run()
    assert [m.version for m in second] == [2]
    assert runner.applied_versions() == [1, 2]


def test_discovery_ignores_non_numeric_prefixes(db_path: Path, tmp_migrations: Path):
    write_migration(tmp_migrations, 1, "good", "CREATE TABLE g (id INTEGER PRIMARY KEY);")
    # malformed prefix — discovery should skip + warn
    (tmp_migrations / "draft_someday.sql").write_text(
        "CREATE TABLE never (id INTEGER PRIMARY KEY);"
    )

    runner = MigrationRunner(db_path, tmp_migrations)
    applied = runner.run()
    assert [m.version for m in applied] == [1]

    conn = sqlite3.connect(str(db_path))
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    conn.close()
    assert "g" in names
    assert "never" not in names


def test_run_migrations_helper_uses_bundled_dir(db_path: Path):
    """The high-level run_migrations() function defaults to the bundled
    src/subarr/migrations/ directory — which contains 001_baseline.sql."""
    applied = run_migrations(db_path)
    assert applied, "expected at least the baseline to apply on fresh DB"
    assert applied[0].name == "001_baseline"
    # And every shipped subarr table exists
    conn = sqlite3.connect(str(db_path))
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    conn.close()
    expected = {
        "scans", "subs_generated", "schedule_config", "auto_queue_rules",
        "lang_enrichment", "media_probe", "pending_walks",
        "pending_decisions", "schema_versions",
    }
    assert expected.issubset(tables), f"missing: {expected - tables}"


def test_existing_v0x_db_upgrades_cleanly(db_path: Path):
    """A user upgrading from v0.x already has the tables. The baseline
    migration uses IF NOT EXISTS so it's a no-op against their data, but
    the version row gets recorded. Future migrations stack on top."""
    # Simulate existing v0.x install: create tables manually first
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE scans (id TEXT PRIMARY KEY, created_at REAL NOT NULL,
                            status TEXT NOT NULL, reverse INTEGER NOT NULL DEFAULT 0,
                            current_index INTEGER NOT NULL DEFAULT 0,
                            paths_json TEXT NOT NULL, results_json TEXT NOT NULL);
        INSERT INTO scans VALUES ('abc', 1000.0, 'done', 0, 0, '[]', '[]');
    """)
    conn.close()

    applied = run_migrations(db_path)
    # Baseline (001) applies as a no-op against existing tables;
    # any further bundled migrations (002+) apply too. Just assert
    # 001 is included and the user's data survived.
    assert 1 in [m.version for m in applied]

    # User's data intact + new tables present
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT id FROM scans WHERE id='abc'").fetchone()
    assert row == ("abc",)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "media_probe" in tables
    conn.close()
