"""SQLite cache for ProbeResult.

Keyed by canonical_path. Entries invalidate when the underlying file's
mtime or size changes. Walks should call `get(canonical, mtime, size)`
first; on miss, run ffprobe + `upsert(...)`. This makes the deep-probe
walk idempotent (re-running over an unchanged folder is a no-op except
for the directory scan).

Lives in the same subarr.db file as the scan store / provenance ledger;
separate connection so background walks don't contend with HTTP-request
writes.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .media_probe import AudioStream, ProbeResult, SubtitleStream


_SCHEMA = """
CREATE TABLE IF NOT EXISTS media_probe (
    canonical_path  TEXT PRIMARY KEY,
    mtime           REAL NOT NULL,
    size            INTEGER NOT NULL,
    duration_s      REAL,
    audio_json      TEXT NOT NULL,
    sub_json        TEXT NOT NULL,
    probed_at       REAL NOT NULL
);
"""


class ProbeStore:
    def __init__(self, db_path: Path):
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

    def get(self, canonical_path: str, mtime: float | None = None,
            size: int | None = None) -> ProbeResult | None:
        """If mtime/size supplied, returns the cached entry only if both
        match; mismatch is treated as cache miss (caller re-probes).
        If mtime/size are None, returns whatever is cached (used by the
        non-strict lookup path in coverage_engine, where the file might
        not even exist on subarr's mount)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT canonical_path, mtime, size, duration_s, audio_json, sub_json, probed_at "
                "FROM media_probe WHERE canonical_path = ?",
                (canonical_path,),
            ).fetchone()
        if not row:
            return None
        cached_mtime, cached_size = row[1], row[2]
        if mtime is not None and abs(mtime - cached_mtime) > 1:
            return None
        if size is not None and size != cached_size:
            return None

        result = ProbeResult(canonical_path=row[0])
        result.duration_s = row[3]
        result.probed_at = row[6]
        result.cached = True
        try:
            audio = json.loads(row[4] or "[]")
            subs = json.loads(row[5] or "[]")
        except (ValueError, TypeError):
            return None
        result.audio = [AudioStream(**a) for a in audio if isinstance(a, dict)]
        result.subtitles = [SubtitleStream(**s) for s in subs if isinstance(s, dict)]
        return result

    def upsert(self, *, canonical_path: str, mtime: float, size: int,
               result: ProbeResult) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO media_probe "
                "(canonical_path, mtime, size, duration_s, audio_json, sub_json, probed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(canonical_path) DO UPDATE SET "
                "  mtime=excluded.mtime, size=excluded.size, "
                "  duration_s=excluded.duration_s, audio_json=excluded.audio_json, "
                "  sub_json=excluded.sub_json, probed_at=excluded.probed_at",
                (
                    canonical_path, mtime, size, result.duration_s,
                    json.dumps([a.to_dict() for a in result.audio]),
                    json.dumps([s.to_dict() for s in result.subtitles]),
                    time.time(),
                ),
            )

    def all_paths(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT canonical_path FROM media_probe"
            ).fetchall()
        return [r[0] for r in rows]
