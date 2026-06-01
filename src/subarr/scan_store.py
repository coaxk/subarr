"""SQLite store for the multi-folder scan queue.

One row per scan request; the path list and per-path results live in JSON columns
because they vary in length and the schema would otherwise need a second table
just for ordering. JSON columns are cheap and queries against them are rare
(only the scan_id GET hits them).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SCAN_STATUS_PENDING = "pending"
SCAN_STATUS_RUNNING = "running"
SCAN_STATUS_DONE = "done"
SCAN_STATUS_ERROR = "error"
SCAN_STATUS_CANCELLED = "cancelled"

PATH_STATUS_PENDING = "pending"
PATH_STATUS_RUNNING = "running"
PATH_STATUS_OK = "ok"           # subgen accepted + queued at least one file
PATH_STATUS_SKIPPED = "skipped"  # subgen walked + skipped all (e.g. embedded EN
                                 # already present, audio language matches
                                 # SKIP_IF_AUDIO_LANGUAGES, etc.)
PATH_STATUS_EMPTY = "empty"      # subgen returned 404 / walked == 0
PATH_STATUS_ERROR = "error"
# #229: subgen restarted between our submission (PATH_STATUS_OK) and the
# .srt landing on disk. Subgen's in-memory queue evaporated, so the work
# we counted as "in-flight" is gone. The restart_watchdog marks these so
# the UI surfaces them in a "Lost on restart" bucket and so the
# completion_watcher stops waiting on them.
PATH_STATUS_ORPHANED = "orphaned"


@dataclass
class PathResult:
    path: str
    status: str = PATH_STATUS_PENDING
    subgen_status_code: int | None = None
    subgen_body: dict[str, Any] | None = None
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "status": self.status,
            "subgen_status_code": self.subgen_status_code,
            "subgen_body": self.subgen_body,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PathResult":
        return cls(**d)


@dataclass
class Scan:
    id: str
    created_at: float
    status: str
    reverse: bool
    current_index: int
    paths: list[str]
    results: list[PathResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "status": self.status,
            "reverse": self.reverse,
            "current_index": self.current_index,
            "paths": self.paths,
            "results": [r.to_dict() for r in self.results],
        }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id            TEXT PRIMARY KEY,
    created_at    REAL NOT NULL,
    status        TEXT NOT NULL,
    reverse       INTEGER NOT NULL DEFAULT 0,
    current_index INTEGER NOT NULL DEFAULT 0,
    paths_json    TEXT NOT NULL,
    results_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scans_created_at ON scans (created_at DESC);
"""


class ScanStore:
    """Thread-safe SQLite wrapper. Reads/writes serialise on a single Lock so
    we don't have to thread per-thread connections through the runner."""

    def __init__(self, db_path: Path):
        self._path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()

    def init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create(self, paths: list[str], reverse: bool) -> Scan:
        scan = Scan(
            id=uuid.uuid4().hex[:16],
            created_at=time.time(),
            status=SCAN_STATUS_PENDING,
            reverse=reverse,
            current_index=0,
            paths=list(paths),
            results=[PathResult(path=p) for p in paths],
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO scans (id, created_at, status, reverse, current_index, paths_json, results_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    scan.id,
                    scan.created_at,
                    scan.status,
                    1 if scan.reverse else 0,
                    scan.current_index,
                    json.dumps(scan.paths),
                    json.dumps([r.to_dict() for r in scan.results]),
                ),
            )
        return scan

    def get(self, scan_id: str) -> Scan | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, created_at, status, reverse, current_index, paths_json, results_json "
                "FROM scans WHERE id = ?",
                (scan_id,),
            ).fetchone()
        if row is None:
            return None
        return Scan(
            id=row[0],
            created_at=row[1],
            status=row[2],
            reverse=bool(row[3]),
            current_index=row[4],
            paths=json.loads(row[5]),
            results=[PathResult.from_dict(d) for d in json.loads(row[6])],
        )

    def save(self, scan: Scan) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE scans SET status = ?, current_index = ?, results_json = ? WHERE id = ?",
                (
                    scan.status,
                    scan.current_index,
                    json.dumps([r.to_dict() for r in scan.results]),
                    scan.id,
                ),
            )

    def list_recent(self, since_epoch: float = 0.0, limit: int = 500) -> list[Scan]:
        """v1.1.1 Featured Queue: return scans created after `since_epoch`,
        most recent first. Each Scan carries per-path results so the queue
        view can render skipped/error rows with their reason."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, created_at, status, reverse, current_index, paths_json, results_json "
                "FROM scans WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?",
                (since_epoch, limit),
            ).fetchall()
        return [
            Scan(
                id=row[0], created_at=row[1], status=row[2],
                reverse=bool(row[3]), current_index=row[4],
                paths=json.loads(row[5]),
                results=[PathResult.from_dict(d) for d in json.loads(row[6])],
            )
            for row in rows
        ]

    def delete(self, scan_id: str) -> bool:
        """Drop a single scan from history. Returns True if a row was deleted."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM scans WHERE id = ?", (scan_id,))
            return cur.rowcount > 0

    def delete_where_status_in(self, statuses: list[str],
                               older_than: float | None = None) -> int:
        """Bulk-purge scans matching a status list (e.g. ['done', 'error',
        'skipped']). Optional age cutoff (epoch) restricts to old rows."""
        if not statuses:
            return 0
        placeholders = ",".join(["?"] * len(statuses))
        params: list[Any] = list(statuses)
        sql = f"DELETE FROM scans WHERE status IN ({placeholders})"
        if older_than is not None:
            sql += " AND created_at < ?"
            params.append(older_than)
        with self._lock:
            cur = self._conn.execute(sql, params)
            return cur.rowcount
