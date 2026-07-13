"""#196/#202 — ephemeral /data detection + retention telemetry signals."""

from __future__ import annotations

import time


def test_ephemeral_returns_none_outside_container(subarr_env, tmp_path, monkeypatch):
    from subarr import data_persistence

    # No /.dockerenv → not a container → unknown, never warns.
    monkeypatch.setattr(data_persistence.os.path, "exists", lambda p: False)
    assert data_persistence.data_dir_is_ephemeral(tmp_path / "subarr.db") is None


def test_ephemeral_true_when_same_device_as_root(subarr_env, tmp_path, monkeypatch):
    from subarr import data_persistence

    monkeypatch.setattr(data_persistence.os.path, "exists", lambda p: True)  # pretend container

    class _St:
        st_dev = 42

    monkeypatch.setattr(data_persistence.os, "stat", lambda p, **kw: _St())
    # data dir and / share a device → /data is the container layer → ephemeral.
    assert data_persistence.data_dir_is_ephemeral(tmp_path / "subarr.db") is True


def test_ephemeral_false_when_distinct_device(subarr_env, tmp_path, monkeypatch):
    from subarr import data_persistence

    monkeypatch.setattr(data_persistence.os.path, "exists", lambda p: True)

    def _stat(p, **kw):
        class _St:
            st_dev = 1 if str(p) == "/" else 2  # different device = a real mount

        return _St()

    monkeypatch.setattr(data_persistence.os, "stat", _stat)
    assert data_persistence.data_dir_is_ephemeral(tmp_path / "subarr.db") is False


def test_check_records_failure_when_ephemeral(subarr_env, tmp_path, monkeypatch):
    from subarr import data_persistence
    from subarr.migrate import run_migrations
    from subarr.task_health import TaskHealthStore

    db = tmp_path / "h.db"
    run_migrations(db)
    health = TaskHealthStore(db)
    monkeypatch.setattr(data_persistence, "data_dir_is_ephemeral", lambda p: True)
    # ephemeral → returns False (NOT persistent) and records a loud failure.
    assert data_persistence.check_data_persistence(db, health) is False
    states = {s.task_name: s for s in health.states()}
    assert states["data-persistence"].last_error_type is not None


def test_check_returns_true_and_success_when_persistent(subarr_env, tmp_path, monkeypatch):
    from subarr import data_persistence
    from subarr.migrate import run_migrations
    from subarr.task_health import TaskHealthStore

    db = tmp_path / "h.db"
    run_migrations(db)
    health = TaskHealthStore(db)
    monkeypatch.setattr(data_persistence, "data_dir_is_ephemeral", lambda p: False)
    assert data_persistence.check_data_persistence(db, health) is True
    states = {s.task_name: s for s in health.states()}
    assert states["data-persistence"].last_success_at is not None
    assert states["data-persistence"].last_error_type is None


def test_check_unknown_records_nothing(subarr_env, tmp_path, monkeypatch):
    from subarr import data_persistence
    from subarr.migrate import run_migrations
    from subarr.task_health import TaskHealthStore

    db = tmp_path / "h.db"
    run_migrations(db)
    health = TaskHealthStore(db)
    monkeypatch.setattr(data_persistence, "data_dir_is_ephemeral", lambda p: None)
    assert data_persistence.check_data_persistence(db, health) is None
    assert health.states() == []  # unknown never surfaces a task


def test_payload_carries_retention_signals(subarr_env, tmp_path):
    from subarr.migrate import run_migrations
    from subarr.telemetry import TelemetryCollector

    db = tmp_path / "t.db"
    run_migrations(db)
    col = TelemetryCollector(db_path=db, stats_provider=lambda: {"data_persistent": True})
    d = col.build_payload().to_dict()
    assert "install_age_days" in d and isinstance(d["install_age_days"], float)
    assert d["install_age_days"] >= 0.0
    assert d["data_persistent"] is True


def test_install_age_grows_with_created_at(subarr_env, tmp_path):
    import sqlite3

    from subarr.migrate import run_migrations
    from subarr.telemetry import TelemetryCollector

    db = tmp_path / "t.db"
    run_migrations(db)
    # Construct first so _ensure_row creates the telemetry_state row, THEN
    # backdate created_at 10 days; age must reflect it.
    col = TelemetryCollector(db_path=db)
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE telemetry_state SET created_at = ? WHERE id = 1", (time.time() - 10 * 86400,))
    conn.commit()
    conn.close()
    age = col.build_payload().to_dict()["install_age_days"]
    assert 9.5 <= age <= 10.5


def test_check_network_fs_records_failure_when_network(subarr_env, tmp_path, monkeypatch):
    from subarr import data_persistence
    from subarr.migrate import run_migrations
    from subarr.task_health import TaskHealthStore

    db = tmp_path / "h.db"
    run_migrations(db)
    health = TaskHealthStore(db)
    monkeypatch.setattr(data_persistence, "data_dir_network_fs", lambda p: "nfs4")
    assert data_persistence.check_network_fs(db, health) == "nfs4"
    states = {s.task_name: s for s in health.states()}
    assert states["data-network-fs"].last_error_type is not None


def test_check_network_fs_success_when_local(subarr_env, tmp_path, monkeypatch):
    from subarr import data_persistence
    from subarr.migrate import run_migrations
    from subarr.task_health import TaskHealthStore

    db = tmp_path / "h.db"
    run_migrations(db)
    health = TaskHealthStore(db)
    monkeypatch.setattr(data_persistence, "data_dir_network_fs", lambda p: None)
    assert data_persistence.check_network_fs(db, health) is None
    states = {s.task_name: s for s in health.states()}
    assert states["data-network-fs"].last_success_at is not None
    assert states["data-network-fs"].last_error_type is None
