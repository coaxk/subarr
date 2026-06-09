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


# Schema (media_probe incl. the v1.1-A `source` column, and probe_failures)
# is owned by migrations/001_baseline.sql + 007_probe_failures.sql +
# 008_init_schema_parity.sql. run_migrations() runs at boot before this
# store — no per-store init_schema(). `source` distinguishes our own
# ffprobe rows from Sonarr/Radarr pre-computed mediaInfo ('arr_mediainfo').


class ProbeStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def get(
        self, canonical_path: str, mtime: float | None = None, size: int | None = None
    ) -> ProbeResult | None:
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

    def upsert(
        self, *, canonical_path: str, mtime: float, size: int, result: ProbeResult, source: str = "ffprobe"
    ) -> None:
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
                    canonical_path,
                    mtime,
                    size,
                    result.duration_s,
                    json.dumps([a.to_dict() for a in result.audio]),
                    json.dumps([s.to_dict() for s in result.subtitles]),
                    time.time(),
                    source,
                ),
            )
            # A successful probe clears any prior failure so a recovered file
            # leaves the "couldn't analyze" bucket.
            self._conn.execute(
                "DELETE FROM probe_failures WHERE canonical_path = ?",
                (canonical_path,),
            )

    # ─── Probe failures (probe-gate "couldn't analyze") ─────────────

    def record_failure(self, canonical_path: str, error: str) -> None:
        """Persist that probing this file failed. Idempotent per path:
        re-failing bumps attempts and refreshes the error/timestamp."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO probe_failures (canonical_path, error, failed_at, attempts) "
                "VALUES (?, ?, ?, 1) "
                "ON CONFLICT(canonical_path) DO UPDATE SET "
                "  error=excluded.error, failed_at=excluded.failed_at, "
                "  attempts=probe_failures.attempts + 1",
                (canonical_path, str(error)[:500], time.time()),
            )

    def failed_paths(self) -> set[str]:
        with self._lock:
            rows = self._conn.execute("SELECT canonical_path FROM probe_failures").fetchall()
        return {r[0] for r in rows}

    def get_failure(self, canonical_path: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT canonical_path, error, failed_at, attempts "
                "FROM probe_failures WHERE canonical_path = ?",
                (canonical_path,),
            ).fetchone()
        if not row:
            return None
        return {"canonical_path": row[0], "error": row[1], "failed_at": row[2], "attempts": row[3]}

    def count_by_source(self) -> dict[str, int]:
        """v1.1-A: telemetry for the arr_mediainfo win. Counts
        {'ffprobe': N, 'arr_mediainfo': M}."""
        with self._lock:
            rows = self._conn.execute("SELECT source, COUNT(*) FROM media_probe GROUP BY source").fetchall()
        return {r[0]: r[1] for r in rows}

    def all_paths(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute("SELECT canonical_path FROM media_probe").fetchall()
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
