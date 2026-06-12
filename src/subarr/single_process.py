"""#204 — exactly one subarr process per database.

Every piece of in-memory state (coverage cache mirror, pending feeder,
scheduler, watchdogs, SQLite WAL assumptions) is built for ONE process. Two
ways users break that silently: `uvicorn --workers 2`-style multi-worker
flags, and the nastier copy-paste accident of two containers mounting the
same /data. Both double-submit the scheduler and corrupt caches with no
error anywhere.

The guard: an OS advisory lock (flock) on `<db dir>/subarr.lock`, held for
the process lifetime. The second process fails to acquire and we surface it
the same way as every silent-failure class — loud log + a red task on the
Health page. We deliberately do NOT refuse to boot: a stale-NFS-lock false
positive that bricked boots would be worse than the warning being ignorable
(and /data-on-NFS is already warned against).

POSIX-only (production is Linux containers). On platforms without fcntl
(Windows dev) the guard reports unknown and stays silent.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

TASK_NAME = "single-process"

try:  # pragma: no cover - import success/failure is platform-determined
    import fcntl

    _LOCK_SUPPORTED = True
except ImportError:  # Windows dev hosts
    fcntl = None  # type: ignore[assignment]
    _LOCK_SUPPORTED = False


class MultipleProcessesError(Exception):
    """Another subarr process already holds this database's lock."""


def acquire_process_lock(db_path: Path):
    """Try to take the exclusive advisory lock for this database.

    Returns an open file handle (KEEP A REFERENCE — the lock lives and dies
    with it) or None when the lock is held elsewhere / unsupported platform /
    any error (never raises)."""
    if not _LOCK_SUPPORTED:
        return None
    try:
        lock_path = Path(db_path).parent / "subarr.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "w")  # noqa: SIM115 - lifetime == process
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return None
        return handle
    except Exception:
        log.debug("process lock acquire failed", exc_info=True)
        return None


def release_process_lock(handle) -> None:
    """Close the handle (drops the flock). Best-effort."""
    try:
        if handle is not None:
            handle.close()
    except Exception:
        pass


def check_single_process(db_path: Path, health):
    """Acquire the lock and record the verdict in task_health.

    Returns the lock handle (caller stores it for the process lifetime) or
    None. On an unsupported platform records nothing and returns None."""
    if not _LOCK_SUPPORTED:
        return None
    handle = acquire_process_lock(db_path)
    try:
        health.register(TASK_NAME, expected_interval_s=None)
        if handle is None:
            err = MultipleProcessesError(
                "another subarr process holds this database's lock — running "
                "multiple workers (or two containers sharing one /data) "
                "double-submits the scheduler and corrupts in-memory state. "
                "Run exactly one subarr process per database."
            )
            health.record_failure(TASK_NAME, err, expected_interval_s=None)
            log.error(
                "MULTIPLE SUBARR PROCESSES DETECTED: another process holds "
                "%s/subarr.lock. Do not run uvicorn with --workers > 1, and do "
                "not point two containers at the same /data.",
                Path(db_path).parent,
            )
        else:
            health.record_success(TASK_NAME, expected_interval_s=None)
    except Exception:
        log.debug("single-process health record failed", exc_info=True)
    return handle
