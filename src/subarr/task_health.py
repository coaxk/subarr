"""#157 Phase 1 — supervised background tasks.

Every long-running loop records the outcome of each cycle in `task_health`
(one upserted row per task). A silent crash — the #79 class where a loop
catches its own exception, logs a warning, and keeps looping forever — now
shows up as `consecutive_failures` climbing (and a captured traceback) instead
of freezing quietly. `/api/health/tasks` reads this; the header pill + Health
page surface it.

Recording is BEST-EFFORT: record_success/record_failure must never raise, or a
failure in the health layer would crash the very loop it monitors.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

from .data_persistence import apply_journal_mode

log = logging.getLogger(__name__)

# Security: tracebacks are served (unauth-reachable on a no-auth install) via
# /api/health/tasks, and some integrations carry their credential in the request
# URL (Tautulli: ?apikey=..., Plex: ?X-Plex-Token=...). httpx embeds the URL in
# its exception text, so a failed call inside a supervised loop could leak the
# key into the stored traceback. Redact secret query-string params before store.
_SECRET_QS = re.compile(
    r"(?i)(api[_-]?key|apikey|token|x-plex-token|password|passwd|secret|access[_-]?token)=([^&\s'\"]+)"
)


def _redact_secrets(text: str) -> str:
    return _SECRET_QS.sub(r"\1=<redacted>", text)


# A task is unhealthy after this many failed cycles in a row...
UNHEALTHY_CONSECUTIVE = 3
# ...or when it hasn't had a SUCCESS in this multiple of its expected cadence
# (catches a loop that's stuck or whose whole asyncio task died and stopped
# updating — not just one that's actively throwing).
STALE_MULTIPLE = 3.0
# Cap the stored traceback so a pathological error can't bloat the row.
_MAX_DETAIL = 4000


@dataclass
class TaskHealth:
    task_name: str
    last_success_at: float | None
    last_error_at: float | None
    last_error_type: str | None
    last_error_detail: str | None
    consecutive_failures: int
    total_runs: int
    total_failures: int
    expected_interval_s: float | None
    updated_at: float

    @property
    def next_run_at(self) -> float | None:
        """#252: estimated next-fire epoch for an interval loop (last success +
        its cadence). None when the cadence is unknown or it hasn't run yet —
        event-driven loops (no expected_interval_s) have no schedule to show."""
        if self.expected_interval_s and self.last_success_at is not None:
            return self.last_success_at + self.expected_interval_s
        return None

    @property
    def is_unhealthy(self) -> bool:
        if self.consecutive_failures >= UNHEALTHY_CONSECUTIVE:
            return True
        # Has run, failed, and never once succeeded → unhealthy.
        if self.last_success_at is None and self.total_failures > 0:
            return True
        # Stale: a cadence is known and no success within STALE_MULTIPLE of it.
        if self.expected_interval_s and self.last_success_at is not None:
            if (time.time() - self.last_success_at) > STALE_MULTIPLE * self.expected_interval_s:
                return True
        return False

    def to_dict(self) -> dict:
        return {
            "task_name": self.task_name,
            "last_success_at": self.last_success_at,
            "last_error_at": self.last_error_at,
            "last_error_type": self.last_error_type,
            "last_error_detail": self.last_error_detail,
            "consecutive_failures": self.consecutive_failures,
            "total_runs": self.total_runs,
            "total_failures": self.total_failures,
            "expected_interval_s": self.expected_interval_s,
            "next_run_at": self.next_run_at,
            "updated_at": self.updated_at,
            "is_unhealthy": self.is_unhealthy,
        }


class TaskHealthStore:
    """Thread-safe SQLite store, one upserted row per task (mirrors update_checks)."""

    def __init__(self, db_path: Path, *, crash_recorder=None):
        self._path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        apply_journal_mode(self._conn, db_path)
        self._lock = threading.Lock()
        # #157 P2: optional callable(exc) -> None. record_failure feeds every
        # supervised-loop exception to it (CrashStore.record), making this the
        # single choke point for the sanitized fleet crash aggregates.
        self._crash_recorder = crash_recorder

    def register(self, task_name: str, *, expected_interval_s: float | None = None) -> None:
        """Seed a row so a task appears (as 'never run yet') before its first
        cycle, and record its cadence for the staleness check. Best-effort."""
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO task_health (task_name, consecutive_failures, total_runs, "
                    "total_failures, expected_interval_s, updated_at) "
                    "VALUES (?, 0, 0, 0, ?, ?) "
                    "ON CONFLICT(task_name) DO UPDATE SET "
                    "  expected_interval_s=COALESCE(excluded.expected_interval_s, task_health.expected_interval_s)",
                    (task_name, expected_interval_s, time.time()),
                )
        except Exception as e:
            log.debug("task_health register(%s) failed: %s", task_name, e)

    def record_success(self, task_name: str, *, expected_interval_s: float | None = None) -> None:
        """A cycle completed cleanly: stamp last_success, reset the failure streak."""
        try:
            now = time.time()
            with self._lock:
                self._conn.execute(
                    "INSERT INTO task_health (task_name, last_success_at, consecutive_failures, "
                    "total_runs, total_failures, expected_interval_s, updated_at) "
                    "VALUES (?, ?, 0, 1, 0, ?, ?) "
                    "ON CONFLICT(task_name) DO UPDATE SET "
                    "  last_success_at=excluded.last_success_at, "
                    "  consecutive_failures=0, "
                    "  total_runs=task_health.total_runs + 1, "
                    "  expected_interval_s=COALESCE(excluded.expected_interval_s, task_health.expected_interval_s), "
                    "  updated_at=excluded.updated_at",
                    (task_name, now, expected_interval_s, now),
                )
        except Exception as e:
            log.debug("task_health record_success(%s) failed: %s", task_name, e)

    def record_failure(
        self, task_name: str, exc: BaseException, *, expected_interval_s: float | None = None
    ) -> None:
        """A cycle raised: capture type + traceback (the silent loops used to
        discard this), bump the failure streak."""
        try:
            # #157 P2: sanitized fleet counter (type + module:line only).
            # Best-effort and FIRST so a SQL hiccup below can't starve it.
            if self._crash_recorder is not None:
                try:
                    self._crash_recorder(exc)
                except Exception:
                    pass
            now = time.time()
            exc_type = type(exc).__name__
            detail = _redact_secrets("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))[
                -_MAX_DETAIL:
            ]
            with self._lock:
                self._conn.execute(
                    "INSERT INTO task_health (task_name, last_error_at, last_error_type, "
                    "last_error_detail, consecutive_failures, total_runs, total_failures, "
                    "expected_interval_s, updated_at) "
                    "VALUES (?, ?, ?, ?, 1, 1, 1, ?, ?) "
                    "ON CONFLICT(task_name) DO UPDATE SET "
                    "  last_error_at=excluded.last_error_at, "
                    "  last_error_type=excluded.last_error_type, "
                    "  last_error_detail=excluded.last_error_detail, "
                    "  consecutive_failures=task_health.consecutive_failures + 1, "
                    "  total_runs=task_health.total_runs + 1, "
                    "  total_failures=task_health.total_failures + 1, "
                    "  expected_interval_s=COALESCE(excluded.expected_interval_s, task_health.expected_interval_s), "
                    "  updated_at=excluded.updated_at",
                    (task_name, now, exc_type, detail, expected_interval_s, now),
                )
        except Exception as e:
            log.debug("task_health record_failure(%s) failed: %s", task_name, e)

    def states(self) -> list[TaskHealth]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT task_name, last_success_at, last_error_at, last_error_type, "
                "last_error_detail, consecutive_failures, total_runs, total_failures, "
                "expected_interval_s, updated_at FROM task_health ORDER BY task_name"
            ).fetchall()
        return [
            TaskHealth(
                task_name=r["task_name"],
                last_success_at=r["last_success_at"],
                last_error_at=r["last_error_at"],
                last_error_type=r["last_error_type"],
                last_error_detail=r["last_error_detail"],
                consecutive_failures=r["consecutive_failures"],
                total_runs=r["total_runs"],
                total_failures=r["total_failures"],
                expected_interval_s=r["expected_interval_s"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def any_unhealthy(self) -> bool:
        return any(t.is_unhealthy for t in self.states())

    def close(self) -> None:
        with self._lock:
            self._conn.close()
