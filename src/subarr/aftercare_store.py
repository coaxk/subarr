"""#156 Track A: persistence for per-job aftercare results. One row per
completed job; latest-per-path surfaced. Mirrors error_store/task_health
(single connection, WAL, lock)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .aftercare import AftercareEvaluation

# Latest-per-path: the most recently INSERTED row for a path (monotonic id
# avoids the equal-timestamp ambiguity MAX(completed_at) had). The full query
# strings are assembled here at MODULE scope — not via f-strings at the
# execute() call site — so bandit doesn't read them as dynamic SQL (B608).
# They contain only fixed literals + bound ? params; no user input is ever
# interpolated (view selects between two constants; limit/offset are bound).
_LATEST = "a.id = (SELECT MAX(b.id) FROM aftercare_results b WHERE b.canonical_path = a.canonical_path)"
_PENDING_COUNT_SQL = (
    f"SELECT COUNT(*) FROM aftercare_results a WHERE a.flagged = 1 AND a.reviewed_at IS NULL AND {_LATEST}"
)
_LIST_ALL_SQL = (
    f"SELECT * FROM aftercare_results a WHERE {_LATEST} "
    "ORDER BY a.flagged DESC, a.completed_at DESC LIMIT ? OFFSET ?"
)
_LIST_FLAGGED_SQL = (
    f"SELECT * FROM aftercare_results a WHERE {_LATEST} "
    "AND a.flagged = 1 AND a.reviewed_at IS NULL "
    "ORDER BY a.flagged DESC, a.completed_at DESC LIMIT ? OFFSET ?"
)


class AfterCareStore:
    def __init__(self, db_path: Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()

    def prune(self, days: int = 365) -> int:
        """#197: one row per completed job, otherwise forever. A year keeps
        the #95 passive-tuning signal window intact while bounding growth.
        PROTECTIVE RULE: flagged-but-unreviewed rows are the user's review
        backlog — never pruned, regardless of age. Run on boot."""
        import time

        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM aftercare_results WHERE completed_at < ? "
                "AND NOT (flagged = 1 AND reviewed_at IS NULL)",
                (time.time() - days * 86400,),
            )
            return cur.rowcount

    def record(
        self,
        *,
        canonical_path: str,
        completed_at: float,
        evaluation: AftercareEvaluation,
        source: str | None,
        preview: str | None = None,
    ) -> None:
        # `preview` (#216) is an already-sanitized snippet of a representative
        # cue, shown in the review UI to explain why an EXTERNAL sub is flagged.
        # NULL for our own (clean) Whisper output.
        with self._lock:
            self._conn.execute(
                "INSERT INTO aftercare_results "
                "(canonical_path, completed_at, composite, cue_count, flagged, "
                " readability_json, signals_json, source, preview, reviewed_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                (
                    canonical_path,
                    completed_at,
                    evaluation.composite,
                    evaluation.cue_count,
                    1 if evaluation.flagged else 0,
                    json.dumps(evaluation.readability) if evaluation.readability else None,
                    json.dumps(evaluation.signals) if evaluation.signals else None,
                    source,
                    preview,
                    time.time(),
                ),
            )

    def pending_count(self) -> int:
        with self._lock:
            row = self._conn.execute(_PENDING_COUNT_SQL).fetchone()
        return int(row[0])

    def list_results(
        self, *, view: str, limit: int, offset: int, source: str | None = None
    ) -> list[dict[str, Any]]:
        # #216: optional source filter (e.g. 'existing_audit') lets the review
        # page separate audited external subs from completion-watcher rows.
        base = _LIST_FLAGGED_SQL if view == "flagged" else _LIST_ALL_SQL
        if source is not None:
            # Splice the source predicate before the trailing ORDER BY clause so
            # the LIMIT/OFFSET params stay last.
            head, sep, tail = base.partition("ORDER BY")
            sql = f"{head}AND a.source = ? {sep}{tail}"
            params: tuple = (source, limit, offset)
        else:
            sql = base
            params = (limit, offset)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get(self, result_id: int) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute(
                "SELECT * FROM aftercare_results WHERE id = ?",
                (result_id,),
            ).fetchone()
        return self._row_to_dict(r) if r else None

    def mark_reviewed(self, result_id: int) -> bool:
        """Mark a result reviewed. Idempotent: returns True if the row exists
        (re-acking a reviewed row is a no-op that still returns True); returns
        False only when no such id exists. Preserves the original review time."""
        with self._lock:
            row = self._conn.execute(
                "SELECT reviewed_at FROM aftercare_results WHERE id = ?",
                (result_id,),
            ).fetchone()
            if row is None:
                return False
            if row["reviewed_at"] is None:
                self._conn.execute(
                    "UPDATE aftercare_results SET reviewed_at = ? WHERE id = ?",
                    (time.time(), result_id),
                )
            return True

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row_to_dict(r: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": r["id"],
            "canonical_path": r["canonical_path"],
            "completed_at": r["completed_at"],
            "composite": r["composite"],
            "cue_count": r["cue_count"],
            "flagged": bool(r["flagged"]),
            "readability": json.loads(r["readability_json"]) if r["readability_json"] else None,
            "signals": json.loads(r["signals_json"]) if r["signals_json"] else None,
            "source": r["source"],
            "preview": r["preview"],
            "reviewed_at": r["reviewed_at"],
            "created_at": r["created_at"],
        }
