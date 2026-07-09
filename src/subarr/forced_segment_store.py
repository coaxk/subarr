"""#364 slice 1 — SQLite cache for forced-segment scan results.

Keyed by canonical_path; a row is a cache HIT only when the stored (mtime, size)
still match the file on disk (mirrors probe_store.py). Schema is owned by
migrations/028_forced_segment_scans.sql — run_migrations() runs at boot before
this store is constructed, so there is no per-store init_schema(). Own
connection, WAL, lock, autocommit — background-walk writes never contend with
HTTP-request writes.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ForcedSegmentScan:
    canonical_path: str
    mtime: float | None
    size: int | None
    # Verdicts written by ForcedSegmentGenerator.process:
    #   scanned         >=1 forced span emitted (sidecar written)
    #   none            qualified + scanned, no foreign scene found
    #   bailed          mostly-foreign / mistagged audio (nothing emitted)
    #   exists          a .forced.en.srt already on disk (no-clobber skip)
    #   vad-unavailable VAD could not run (silero/onnxruntime missing)
    #   error           an unexpected failure was caught and recorded
    # ('cached' and 'skipped' are process RETURN statuses only — a cache hit
    # returns 'cached' but the stored row keeps its original verdict above.)
    status: str
    n_spans: int
    total_ms: int
    scanned_at: float


class ForcedSegmentScanStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # Only TERMINAL verdicts count as a cache hit — the file is settled for this
    # (path, mtime, size). Transient verdicts ('error', 'vad-unavailable') are a
    # deliberate MISS so a subgen outage or an un-pulled VAD model auto-retries on
    # the next walk without the file having to change. upsert still records ALL
    # statuses (last outcome stays visible in summary()); only HIT lookup narrows.
    _TERMINAL_STATUSES = ("scanned", "none", "bailed", "exists")

    def get(
        self, canonical_path: str, mtime: float | None = None, size: int | None = None
    ) -> ForcedSegmentScan | None:
        """Return the cached scan only if the supplied (mtime, size) still match
        (mtime compared with a 1s tolerance, mirroring probe_store) AND the stored
        verdict is terminal. Any mismatch or a transient verdict is a miss so the
        caller re-scans."""
        placeholders = ", ".join("?" for _ in self._TERMINAL_STATUSES)
        with self._lock:
            row = self._conn.execute(
                "SELECT canonical_path, mtime, size, status, n_spans, total_ms, scanned_at "
                f"FROM forced_segment_scans WHERE canonical_path = ? AND status IN ({placeholders})",
                (canonical_path, *self._TERMINAL_STATUSES),
            ).fetchone()
        if not row:
            return None
        if mtime is not None and (row["mtime"] is None or abs(mtime - row["mtime"]) > 1):
            return None
        if size is not None and size != row["size"]:
            return None
        return ForcedSegmentScan(
            canonical_path=row["canonical_path"],
            mtime=row["mtime"],
            size=row["size"],
            status=row["status"],
            n_spans=row["n_spans"],
            total_ms=row["total_ms"],
            scanned_at=row["scanned_at"],
        )

    def upsert(
        self,
        *,
        canonical_path: str,
        mtime: float | None,
        size: int | None,
        status: str,
        n_spans: int,
        total_ms: int,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO forced_segment_scans "
                "(canonical_path, mtime, size, status, n_spans, total_ms, scanned_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(canonical_path) DO UPDATE SET "
                "  mtime=excluded.mtime, size=excluded.size, status=excluded.status, "
                "  n_spans=excluded.n_spans, total_ms=excluded.total_ms, scanned_at=excluded.scanned_at",
                (canonical_path, mtime, size, status, n_spans, total_ms, time.time()),
            )

    def summary(self) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n, MAX(scanned_at) AS last, "
                "COALESCE(SUM(n_spans), 0) AS spans FROM forced_segment_scans"
            ).fetchone()
        return {
            "total_scanned": row["n"] or 0,
            "last_scanned_at": row["last"],
            "total_spans": row["spans"] or 0,
        }
