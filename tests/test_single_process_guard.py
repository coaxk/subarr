"""#204 — exactly one subarr process per database.

All the in-memory state (coverage cache mirror, pending feeder, scheduler,
watchdogs) assumes one process. `uvicorn --workers 2` — or the nastier
copy-paste case, two CONTAINERS sharing one /data — silently double-submits
the scheduler and splits caches. An OS-level advisory lock on
/data/subarr.lock catches both: the second process fails to acquire and
surfaces loudly. POSIX-only by design (production is Linux containers);
on platforms without fcntl the guard reports unknown and stays silent.
"""

from __future__ import annotations

import multiprocessing
import sys

import pytest

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="flock is POSIX; guard is a no-op on Windows")


def _hold_lock(db_path, q):  # pragma: no cover - runs in a child process
    from subarr.single_process import acquire_process_lock

    handle = acquire_process_lock(db_path)
    q.put(handle is not None)
    if handle is not None:
        import time

        time.sleep(10)


def test_acquire_returns_handle_or_none_unknown(subarr_env, tmp_path):
    from subarr import single_process

    handle = single_process.acquire_process_lock(tmp_path / "subarr.db")
    if sys.platform == "win32":
        assert handle is None  # unknown/no-op platform
    else:
        assert handle is not None
        single_process.release_process_lock(handle)


@posix_only
def test_second_process_fails_to_acquire(subarr_env, tmp_path):
    db = tmp_path / "subarr.db"
    q = multiprocessing.Queue()
    p = multiprocessing.Process(target=_hold_lock, args=(db, q))
    p.start()
    try:
        assert q.get(timeout=5) is True  # child holds the lock
        from subarr.single_process import acquire_process_lock

        assert acquire_process_lock(db) is None  # we must NOT get it
    finally:
        p.terminate()
        p.join(timeout=5)


def test_check_records_health_failure_when_lock_held(subarr_env, tmp_path, monkeypatch):
    from subarr import single_process
    from subarr.migrate import run_migrations
    from subarr.task_health import TaskHealthStore

    db = tmp_path / "h.db"
    run_migrations(db)
    health = TaskHealthStore(db)
    # Simulate "another process holds the lock" regardless of platform.
    monkeypatch.setattr(single_process, "acquire_process_lock", lambda p: None)
    monkeypatch.setattr(single_process, "_LOCK_SUPPORTED", True)
    got = single_process.check_single_process(db, health)
    assert got is None  # no handle
    states = {s.task_name: s for s in health.states()}
    assert states["single-process"].last_error_type is not None


def test_check_records_success_when_acquired(subarr_env, tmp_path, monkeypatch):
    from subarr import single_process
    from subarr.migrate import run_migrations
    from subarr.task_health import TaskHealthStore

    db = tmp_path / "h.db"
    run_migrations(db)
    health = TaskHealthStore(db)
    sentinel = object()
    monkeypatch.setattr(single_process, "acquire_process_lock", lambda p: sentinel)
    monkeypatch.setattr(single_process, "_LOCK_SUPPORTED", True)
    assert single_process.check_single_process(db, health) is sentinel
    states = {s.task_name: s for s in health.states()}
    assert states["single-process"].last_success_at is not None


def test_unsupported_platform_is_silent(subarr_env, tmp_path, monkeypatch):
    from subarr import single_process
    from subarr.migrate import run_migrations
    from subarr.task_health import TaskHealthStore

    db = tmp_path / "h.db"
    run_migrations(db)
    health = TaskHealthStore(db)
    monkeypatch.setattr(single_process, "_LOCK_SUPPORTED", False)
    assert single_process.check_single_process(db, health) is None
    assert health.states() == []  # never surfaces a task it can't evaluate
