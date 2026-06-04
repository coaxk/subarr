"""#131 — SQLite persistence for tuning-lab sweeps.

Sweeps (and their ranked results) persist so history survives a restart, and
so they can later feed the federated tournament (#124): the per-recipe scores
keyed by language/content are exactly the signal crowd-curated per-language
presets are built from.

`ArenaRun` is the in-flight + persisted representation (defined here so the
store and service share it without a circular import; re-exported from
arena_service for stable imports). The store mirrors ErrorStore/ScanStore:
single connection + lock + WAL, schema owned by migrations (no init here).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class ArenaRun:
    id: str
    media_path: str
    variants: list[dict[str, Any]]          # [{label, kwargs}]
    source_language: str | None = None
    status: str = "pending"                  # pending | running | done | error
    source_text: str | None = None
    outcomes: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None     # serialized TournamentResult
    error: str | None = None
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> dict[str, Any]:
        """Lightweight shape for the sweeps list — no heavy scorecards. Full
        detail (the ranked table) is fetched per-run via /{id}."""
        return {
            "id": self.id,
            "media_path": self.media_path,
            "status": self.status,
            "recipe_count": len(self.variants),
            "done_count": len(self.outcomes),
            "winner": (self.result or {}).get("winner_label"),
            "error": self.error,
            "created_at": self.created_at,
        }


def _row_to_run(r: sqlite3.Row) -> ArenaRun:
    return ArenaRun(
        id=r["id"],
        media_path=r["media_path"],
        source_language=r["source_language"],
        status=r["status"],
        source_text=r["source_text"],
        variants=json.loads(r["variants"]),
        outcomes=json.loads(r["outcomes"]),
        result=json.loads(r["result"]) if r["result"] else None,
        error=r["error"],
        created_at=r["created_at"],
    )


class ArenaStore:
    """Thread-safe SQLite store for arena sweeps (single conn + lock, WAL)."""

    def __init__(self, db_path: Path):
        self._path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()

    def save(self, run: ArenaRun) -> None:
        """Upsert the full run (write-through on every state transition)."""
        winner = (run.result or {}).get("winner_label")
        with self._lock:
            self._conn.execute(
                """INSERT INTO arena_runs
                     (id, media_path, source_language, status, source_text,
                      variants, outcomes, result, winner, error, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     status=excluded.status, source_text=excluded.source_text,
                     outcomes=excluded.outcomes, result=excluded.result,
                     winner=excluded.winner, error=excluded.error,
                     updated_at=excluded.updated_at""",
                (run.id, run.media_path, run.source_language, run.status, run.source_text,
                 json.dumps(run.variants), json.dumps(run.outcomes),
                 json.dumps(run.result) if run.result is not None else None,
                 winner, run.error, run.created_at, time.time()),
            )

    def get(self, run_id: str) -> ArenaRun | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM arena_runs WHERE id = ?", (run_id,)).fetchone()
        return _row_to_run(row) if row else None

    def list(self, limit: int = 200) -> list[ArenaRun]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM arena_runs ORDER BY created_at DESC LIMIT ?", (limit,),
            ).fetchall()
        return [_row_to_run(r) for r in rows]

    def reconcile_interrupted(self) -> int:
        """A run that was pending/running when the process died can never
        finish — its asyncio task is gone. Mark such rows as errored on boot
        so the UI shows the truth instead of a forever-spinning sweep."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE arena_runs SET status='error', error='interrupted by restart', updated_at=? "
                "WHERE status IN ('pending', 'running')",
                (time.time(),),
            )
            return cur.rowcount or 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()
