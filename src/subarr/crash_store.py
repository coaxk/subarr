"""#157 Phase 2 — sanitized fleet crash aggregates.

Records (exception class, module:line) per background-task failure and
aggregates them into the daily telemetry ping as crash_counts_24h. The
privacy line is the TRANSMIT boundary: this store holds ONLY the exception
type and a module:line location — never messages, tracebacks, or filesystem
paths. The rich local detail (full redacted traceback) lives in task_health
and never leaves the box.

Best-effort like ErrorStore: every public method swallows its own failures
so crash accounting can never take down the loop it observes.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Worker contract: object keys are validated strings, max 64 chars, max 64
# keys per ping. Key format is "{exc_type}:{module}:{line}".
_MAX_KEY = 64
_MAX_KEYS = 64
_MAX_EXC = 40


def crash_location(exc: BaseException) -> str:
    """Innermost subarr frame of the exception as 'module:line'.

    Module names come from frame __name__ with the leading 'subarr.'
    stripped — never file paths. Falls back to the innermost frame of any
    module, then to '?:0'.
    """
    try:
        tb = exc.__traceback__
        best: tuple[str, int] | None = None
        while tb is not None:
            mod = tb.tb_frame.f_globals.get("__name__", "") or ""
            lineno = tb.tb_lineno
            if mod.startswith("subarr.") or mod == "subarr":
                best = (mod.removeprefix("subarr."), lineno)
            elif best is None:
                best = (mod or "?", lineno)
            tb = tb.tb_next
        if best is None:
            return "?:0"
        mod, lineno = best
        # Belt-and-braces: a module name can never contain path separators,
        # but the worker key rules are absolute — enforce here too.
        mod = mod.replace("/", "_").replace("\\", "_")
        return f"{mod}:{lineno}"
    except Exception:
        return "?:0"


class CrashStore:
    """Append-only (exc_type, location, occurred_at) rows in crash_events."""

    def __init__(self, db_path: Path):
        self._path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        # #291: WAL is persistent but re-issue so this store is boot-order-independent.
        conn = sqlite3.connect(str(self._path), isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def record(self, exc: BaseException, when: float | None = None) -> None:
        """Best-effort insert; never raises (a crash recorder that crashes
        the loop it observes would be the #79 bug all over again)."""
        try:
            exc_type = type(exc).__name__[:_MAX_EXC]
            location = crash_location(exc)
            # Budget the full key to the worker's 64-char limit.
            location = location[: _MAX_KEY - len(exc_type) - 1]
            with self._lock, self._connect() as conn:
                conn.execute(
                    "INSERT INTO crash_events (exc_type, location, occurred_at) VALUES (?,?,?)",
                    (exc_type, location, when if when is not None else time.time()),
                )
        except Exception:
            log.debug("crash_store record failed", exc_info=True)

    def counts_since(self, cutoff: float) -> dict[str, int]:
        """{'ExcType:module:line': count} since cutoff, capped at the
        worker's 64-key limit (highest counts kept)."""
        try:
            with self._lock, self._connect() as conn:
                rows = conn.execute(
                    "SELECT exc_type || ':' || location AS key, COUNT(*) AS n "
                    "FROM crash_events WHERE occurred_at >= ? "
                    "GROUP BY key ORDER BY n DESC LIMIT ?",
                    (cutoff, _MAX_KEYS),
                ).fetchall()
            return {r[0]: r[1] for r in rows}
        except Exception:
            log.debug("crash_store counts_since failed", exc_info=True)
            return {}

    def prune(self, days: int = 30) -> None:
        """Drop rows older than the aggregation window needs. Run on boot."""
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    "DELETE FROM crash_events WHERE occurred_at < ?",
                    (time.time() - days * 86400,),
                )
        except Exception:
            log.debug("crash_store prune failed", exc_info=True)
