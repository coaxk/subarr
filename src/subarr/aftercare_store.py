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


class AfterCareStore:
    def __init__(self, db_path: Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(db_path), check_same_thread=False, isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()

    def record(self, *, canonical_path: str, completed_at: float,
               evaluation: AftercareEvaluation, source: str | None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO aftercare_results "
                "(canonical_path, completed_at, composite, cue_count, flagged, "
                " readability_json, signals_json, source, reviewed_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                (
                    canonical_path, completed_at, evaluation.composite,
                    evaluation.cue_count, 1 if evaluation.flagged else 0,
                    json.dumps(evaluation.readability) if evaluation.readability else None,
                    json.dumps(evaluation.signals) if evaluation.signals else None,
                    source, time.time(),
                ),
            )

    # latest-per-path: a row is "current" iff no newer row exists for its path.
    _LATEST = (
        "a.completed_at = (SELECT MAX(b.completed_at) FROM aftercare_results b "
        "WHERE b.canonical_path = a.canonical_path)"
    )

    def pending_count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                f"SELECT COUNT(*) FROM aftercare_results a "
                f"WHERE a.flagged = 1 AND a.reviewed_at IS NULL AND {self._LATEST}"
            ).fetchone()
        return int(row[0])

    def list_results(self, *, view: str, limit: int, offset: int) -> list[dict[str, Any]]:
        where = self._LATEST
        if view == "flagged":
            where += " AND a.flagged = 1 AND a.reviewed_at IS NULL"
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM aftercare_results a WHERE {where} "
                f"ORDER BY a.flagged DESC, a.completed_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get(self, result_id: int) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute(
                "SELECT * FROM aftercare_results WHERE id = ?", (result_id,),
            ).fetchone()
        return self._row_to_dict(r) if r else None

    def mark_reviewed(self, result_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE aftercare_results SET reviewed_at = ? "
                "WHERE id = ? AND reviewed_at IS NULL",
                (time.time(), result_id),
            )
            return cur.rowcount > 0

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
            "reviewed_at": r["reviewed_at"],
        }
