"""#196 — boot-time database integrity check.

Everything irreplaceable (verifications, intents, provenance) lives in one
SQLite file. A corrupted DB must be LOUD on the Health page, not silently
half-working. quick_check on boot feeds the existing task_health surface.
"""

from __future__ import annotations


def _stores(tmp_path):
    from subarr.migrate import run_migrations
    from subarr.task_health import TaskHealthStore

    db = tmp_path / "t.db"
    run_migrations(db)
    return db, TaskHealthStore(db)


def test_healthy_db_records_success(subarr_env, tmp_path):
    from subarr.db_integrity import check_db_integrity

    db, health = _stores(tmp_path)
    assert check_db_integrity(db, health) is True
    states = {s.task_name: s for s in health.states()}
    assert "db-integrity" in states
    assert states["db-integrity"].last_success_at is not None
    assert states["db-integrity"].consecutive_failures == 0


def test_corrupt_db_records_failure_loudly(subarr_env, tmp_path):
    from subarr.db_integrity import check_db_integrity
    from subarr.migrate import run_migrations
    from subarr.task_health import TaskHealthStore

    # Health rows live in a SEPARATE healthy db so the failure is recordable
    # even when the main db is toast (mirrors the real risk split poorly but
    # exercises the failure path; in production both share a file and a
    # hard-corrupt db will surface via the boot exception instead).
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not a sqlite database, it is a haiku\n" * 100)
    health_db = tmp_path / "health.db"
    run_migrations(health_db)
    health = TaskHealthStore(health_db)

    assert check_db_integrity(bad, health) is False
    states = {s.task_name: s for s in health.states()}
    assert states["db-integrity"].last_error_type is not None
    assert states["db-integrity"].consecutive_failures >= 1


def test_check_never_raises(subarr_env, tmp_path):
    from subarr.db_integrity import check_db_integrity
    from subarr.migrate import run_migrations
    from subarr.task_health import TaskHealthStore

    health_db = tmp_path / "h.db"
    run_migrations(health_db)
    health = TaskHealthStore(health_db)
    # Nonexistent path: must return False, not raise (boot must continue).
    assert check_db_integrity(tmp_path / "missing" / "no.db", health) is False
