"""Anonymous error-class event log, feeding telemetry's error_counts_30d.

Stores ONLY the exception class name + a timestamp -- never the message --
so the public stats dashboard can report failure-class counts without
leaking paths / titles / keys. Schema lives in migrations/006; this store
does NOT init_schema (migrations are the sole schema path).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)


class ErrorStore:
    """Thread-safe SQLite wrapper (single conn + lock, matching ScanStore)."""

    def __init__(self, db_path: Path):
        self._path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()

    def record(self, exc_class: str, *, when: float | None = None) -> None:
        """Record one error occurrence by class name. Best-effort: never
        raises into the caller's hot path (a telemetry write must not break
        a scan). Only the bare class name is stored (truncated for safety)."""
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO error_events (exc_class, occurred_at) VALUES (?, ?)",
                    (str(exc_class)[:80], when if when is not None else time.time()),
                )
        except Exception as e:  # pragma: no cover - defensive
            log.debug("error_store.record failed: %s", e)

    def counts_since(self, since_epoch: float) -> dict[str, int]:
        """{exc_class: count} for events at or after since_epoch."""
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT exc_class, COUNT(*) FROM error_events WHERE occurred_at >= ? GROUP BY exc_class",
                    (since_epoch,),
                ).fetchall()
            return {r[0]: int(r[1]) for r in rows}
        except Exception as e:  # pragma: no cover - defensive
            log.debug("error_store.counts_since failed: %s", e)
            return {}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
