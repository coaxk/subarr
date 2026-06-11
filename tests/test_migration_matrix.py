"""#200 — every released schema must upgrade cleanly to HEAD.

The fleet skews old (the telemetry outage revealed ~2,000 installs on
1.1.0/1.2.x), so the common real-world upgrade is a many-migration jump
(e.g. 008 → 018 in one pull), a path never exercised by fresh-DB tests.

Mechanism: migrations are immutable once shipped (verified 2026-06-12:
001-008 byte-identical since v1.0.0), so each release's schema is exactly
"the first N current migration files applied in order". For each historical
cut: apply files [0..N) into a fresh DB, plant representative user data,
upgrade with the FULL current migration set, then assert the data survived
and the boot-critical stores work.

If a future release adds migrations, no change is needed here (HEAD moves);
add a new cut entry when a release ships.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

MIGRATIONS_DIR = Path(__file__).parent.parent / "src" / "subarr" / "migrations"

# Highest migration shipped in each release line (gh: git ls-tree <tag>).
# v1.0.x and v1.1.0 shipped 001-008; v1.2.x 001-011; v1.3.0 001-015;
# v1.4.0 001-017. Add new entries as releases ship.
RELEASE_CUTS = {
    "v1.0-1.1": 8,
    "v1.2.x": 11,
    "v1.3.0": 15,
    "v1.4.0": 17,
}


def _all_migration_files() -> list[Path]:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    assert len(files) >= 18, f"expected >=18 migrations, found {len(files)}"
    return files


def _apply_cut(db: Path, tmp_path: Path, cut: int) -> None:
    """Build a historical-era DB: apply only the first `cut` migrations."""
    from subarr.migrate import run_migrations

    era_dir = tmp_path / f"migrations_{cut}"
    era_dir.mkdir()
    for f in _all_migration_files()[:cut]:
        shutil.copy(f, era_dir / f.name)
    applied = run_migrations(db, migrations_dir=era_dir)
    assert len(applied) == cut


def _plant_user_data(db: Path) -> None:
    """Rows in baseline-era tables that MUST survive any upgrade."""
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO subs_generated (canonical_path, source, queued_at) VALUES (?,?,?)",
        ("TV/Matrix-Test/Season 1/ep.mkv", "subgenscan", 1780000000.0),
    )
    conn.execute(
        "INSERT INTO scans (id, created_at, status, paths_json, results_json) VALUES (?,?,?,?,?)",
        ("matrixtest01", 1780000000.0, "completed", "[]", "[]"),
    )
    conn.commit()
    conn.close()


def _boot_smoke(db: Path) -> None:
    """Exercise the stores app.py builds at lifespan against the upgraded DB."""
    from subarr.coverage_cache import CoverageCache
    from subarr.crash_store import CrashStore
    from subarr.error_store import ErrorStore
    from subarr.probe_store import ProbeStore
    from subarr.scan_store import ScanStore
    from subarr.task_health import TaskHealthStore
    from subarr.telemetry import TelemetryCollector

    assert ProbeStore(db).all_paths() == []
    assert ScanStore(db).get("matrixtest01") is not None
    assert TaskHealthStore(db).states() == []
    assert CrashStore(db).counts_since(0) == {}
    assert ErrorStore(db).counts_since(0) == {}
    cache = CoverageCache(db)
    cache.load()  # no snapshot row -> stays None, must not raise
    col = TelemetryCollector(db_path=db)  # _ensure_row creates telemetry_state
    assert len(col.state().install_id) == 32


@pytest.mark.parametrize("release,cut", sorted(RELEASE_CUTS.items(), key=lambda kv: kv[1]))
def test_release_schema_upgrades_to_head(subarr_env, tmp_path, release, cut):
    from subarr.migrate import MigrationRunner, run_migrations

    db = tmp_path / f"upgrade_{cut}.db"
    _apply_cut(db, tmp_path, cut)
    _plant_user_data(db)

    # The upgrade a real install experiences on `docker compose pull`.
    applied = run_migrations(db)
    total = len(_all_migration_files())
    assert len(applied) == total - cut, f"{release}: expected {total - cut} new migrations"
    assert MigrationRunner(db, MIGRATIONS_DIR).applied_versions() == list(range(1, total + 1))

    # User data planted on the old schema survives.
    conn = sqlite3.connect(str(db))
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM subs_generated WHERE canonical_path = 'TV/Matrix-Test/Season 1/ep.mkv'"
        ).fetchone()[0]
        == 1
    )
    assert conn.execute("SELECT status FROM scans WHERE id = 'matrixtest01'").fetchone()[0] == "completed"
    conn.close()

    _boot_smoke(db)

    # Idempotence: a second run (container restart) applies nothing.
    assert run_migrations(db) == []


def test_fresh_install_matches_upgraded_schema(subarr_env, tmp_path):
    """A fresh HEAD install and a v1.0-era install upgraded to HEAD must end
    at the same schema (same tables, same columns) — divergence here means a
    migration and the baseline disagree."""
    from subarr.migrate import run_migrations

    fresh = tmp_path / "fresh.db"
    run_migrations(fresh)
    upgraded = tmp_path / "upgraded.db"
    _apply_cut(upgraded, tmp_path, 8)
    run_migrations(upgraded)

    def schema(db: Path) -> dict[str, list[str]]:
        conn = sqlite3.connect(str(db))
        out = {}
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall():
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({name})").fetchall()]
            out[name] = sorted(cols)
        conn.close()
        return out

    assert schema(fresh) == schema(upgraded)
