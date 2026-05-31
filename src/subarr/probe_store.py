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
    probed_at       REAL NOT NULL,
    source          TEXT NOT NULL DEFAULT 'ffprobe'
);
"""

# v1.1-A migration: add `source` column to track whether the row came from
# our own ffprobe or from Sonarr/Radarr's pre-computed mediaInfo. Cheap to
# add with DEFAULT; older rows get 'ffprobe' which is what they actually
# were. New 'arr_mediainfo' rows are upgradable to 'ffprobe' on next walk
# (richer data wins).
_MIGRATE_SOURCE_COL = """
ALTER TABLE media_probe ADD COLUMN source TEXT NOT NULL DEFAULT 'ffprobe'
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
            # Best-effort migration for pre-v1.1-A DBs. ALTER fails harmlessly
            # if the column already exists (SQLite raises OperationalError).
            try:
                self._conn.execute(_MIGRATE_SOURCE_COL)
            except sqlite3.OperationalError:
                pass

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
               result: ProbeResult, source: str = "ffprobe") -> None:
        """Upsert a probe row. `source` defaults to 'ffprobe' to preserve
        v1.0 behavior.

        v1.1-A: when source='arr_mediainfo' we DO NOT clobber an existing
        ffprobe row — ffprobe is richer (per-track default/forced/hi flags,
        codec details) so it always wins. Arr data is a backstop for files
        ffprobe hasn't touched yet."""
        with self._lock:
            if source == "arr_mediainfo":
                existing = self._conn.execute(
                    "SELECT source FROM media_probe WHERE canonical_path = ?",
                    (canonical_path,),
                ).fetchone()
                if existing and existing[0] == "ffprobe":
                    return
            self._conn.execute(
                "INSERT INTO media_probe "
                "(canonical_path, mtime, size, duration_s, audio_json, sub_json, probed_at, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(canonical_path) DO UPDATE SET "
                "  mtime=excluded.mtime, size=excluded.size, "
                "  duration_s=excluded.duration_s, audio_json=excluded.audio_json, "
                "  sub_json=excluded.sub_json, probed_at=excluded.probed_at, "
                "  source=excluded.source",
                (
                    canonical_path, mtime, size, result.duration_s,
                    json.dumps([a.to_dict() for a in result.audio]),
                    json.dumps([s.to_dict() for s in result.subtitles]),
                    time.time(), source,
                ),
            )

    def count_by_source(self) -> dict[str, int]:
        """v1.1-A: telemetry for the arr_mediainfo win. Counts
        {'ffprobe': N, 'arr_mediainfo': M}."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT source, COUNT(*) FROM media_probe GROUP BY source"
            ).fetchall()
        return {r[0]: r[1] for r in rows}

    def all_paths(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT canonical_path FROM media_probe"
            ).fetchall()
        return [r[0] for r in rows]

    def all_entries(self) -> list[ProbeResult]:
        """Return every cached probe as a hydrated ProbeResult. Used by
        the Library Probe tab to render the full cache."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT canonical_path, mtime, size, duration_s, audio_json, sub_json, probed_at "
                "FROM media_probe ORDER BY canonical_path"
            ).fetchall()
        out: list[ProbeResult] = []
        for row in rows:
            try:
                audio = json.loads(row[4] or "[]")
                subs = json.loads(row[5] or "[]")
            except (ValueError, TypeError):
                continue
            r = ProbeResult(canonical_path=row[0])
            r.duration_s = row[3]
            r.probed_at = row[6]
            r.cached = True
            r.audio = [AudioStream(**a) for a in audio if isinstance(a, dict)]
            r.subtitles = [SubtitleStream(**s) for s in subs if isinstance(s, dict)]
            out.append(r)
        return out
