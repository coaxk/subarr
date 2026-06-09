"""Provenance ledger.

Records every transcribe job subarr submits to subgen. Two distinct
purposes:

1. **Source-of-truth for "who wrote this sub?"** — the v1.1
   `GET /api/provenance/<path>` endpoint joins this ledger with Bazarr's
   history endpoint + the filename suffix to answer that question
   end-to-end. srt-cleaner integration (later) reads this ledger to
   know which subs to evaluate.

2. **Completion tracking** — rows are written at submit time with
   completed_at=NULL. The completion-watcher background task polls
   subgen's /queue, marks completed_at when the path leaves both
   queued+processing, and (if a series_id is known) triggers Bazarr's
   scan-disk so Bazarr picks up the new sub without waiting for its
   periodic scan.

Same SQLite file as the scan store. WAL mode + a single Lock around
the connection — no per-thread state.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


SOURCE_SUBGENSCAN = "subgenscan"
SOURCE_BAZARR_VIA_SUBGEN = "bazarr-via-subgen"
SOURCE_BAZARR_EXTERNAL = "bazarr-external"


@dataclass
class LedgerEntry:
    id: int
    canonical_path: str
    series_id: int | None
    sonarr_episode_id: int | None
    radarr_movie_id: int | None
    scan_id: str | None
    source: str
    subgen_version: str | None
    queued_at: float
    completed_at: float | None
    bazarr_scan_triggered_at: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "canonical_path": self.canonical_path,
            "series_id": self.series_id,
            "sonarr_episode_id": self.sonarr_episode_id,
            "radarr_movie_id": self.radarr_movie_id,
            "scan_id": self.scan_id,
            "source": self.source,
            "subgen_version": self.subgen_version,
            "queued_at": self.queued_at,
            "completed_at": self.completed_at,
            "bazarr_scan_triggered_at": self.bazarr_scan_triggered_at,
        }


# Schema (subs_generated + indexes) is owned by migrations/001_baseline.sql.
# run_migrations() runs at boot before this store — no per-store init_schema().


class ProvenanceStore:
    """Same SQLite file as ScanStore; separate connection so we don't
    contend on the scan store's lock during the completion watcher's
    background polling."""

    def __init__(self, db_path: Path):
        self._path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def record(
        self,
        *,
        canonical_path: str,
        scan_id: str | None,
        source: str = SOURCE_SUBGENSCAN,
        series_id: int | None = None,
        sonarr_episode_id: int | None = None,
        radarr_movie_id: int | None = None,
        subgen_version: str | None = None,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO subs_generated "
                "(canonical_path, series_id, sonarr_episode_id, radarr_movie_id, "
                " scan_id, source, subgen_version, queued_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    canonical_path,
                    series_id,
                    sonarr_episode_id,
                    radarr_movie_id,
                    scan_id,
                    source,
                    subgen_version,
                    time.time(),
                ),
            )
            return cur.lastrowid

    def pending(self) -> list[LedgerEntry]:
        """All entries where subgen hasn't reported completion yet.
        Watcher reads this every poll cycle."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, canonical_path, series_id, sonarr_episode_id, radarr_movie_id, "
                "       scan_id, source, subgen_version, queued_at, completed_at, "
                "       bazarr_scan_triggered_at "
                "FROM subs_generated WHERE completed_at IS NULL"
            ).fetchall()
        return [LedgerEntry(*r) for r in rows]

    def mark_completed(self, ledger_id: int, when: float | None = None) -> None:
        ts = when if when is not None else time.time()
        with self._lock:
            self._conn.execute(
                "UPDATE subs_generated SET completed_at = ? WHERE id = ?",
                (ts, ledger_id),
            )

    def mark_bazarr_triggered(self, ledger_id: int, when: float | None = None) -> None:
        ts = when if when is not None else time.time()
        with self._lock:
            self._conn.execute(
                "UPDATE subs_generated SET bazarr_scan_triggered_at = ? WHERE id = ?",
                (ts, ledger_id),
            )

    def query_by_path(self, canonical_path: str, limit: int = 20) -> list[LedgerEntry]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, canonical_path, series_id, sonarr_episode_id, radarr_movie_id, "
                "       scan_id, source, subgen_version, queued_at, completed_at, "
                "       bazarr_scan_triggered_at "
                "FROM subs_generated WHERE canonical_path = ? "
                "ORDER BY queued_at DESC LIMIT ?",
                (canonical_path, limit),
            ).fetchall()
        return [LedgerEntry(*r) for r in rows]

    def recent(self, limit: int = 50) -> list[LedgerEntry]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, canonical_path, series_id, sonarr_episode_id, radarr_movie_id, "
                "       scan_id, source, subgen_version, queued_at, completed_at, "
                "       bazarr_scan_triggered_at "
                "FROM subs_generated ORDER BY queued_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [LedgerEntry(*r) for r in rows]

    def completed_paths_since(self, since_epoch: float) -> set[str]:
        """#229 reconciliation helper: canonical paths whose transcription
        completed (completed_at IS NOT NULL) on or after since_epoch. Used
        by ScanStore.mark_orphaned_before to NOT orphan paths that subgen
        actually finished before the restart."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT canonical_path FROM subs_generated "
                "WHERE completed_at IS NOT NULL AND completed_at >= ?",
                (since_epoch,),
            ).fetchall()
        return {r[0] for r in rows}

    def completed_without_bazarr(self, max_age_s: int = 86400) -> list[LedgerEntry]:
        """Entries that completed transcribe but never fired Bazarr's
        scan-disk task. Retry fodder for the completion watcher.

        `max_age_s` bounds how far back we'll look — avoids triggering a
        forever-old retry storm after a long subarr downtime."""
        import time

        cutoff = time.time() - max_age_s
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, canonical_path, series_id, sonarr_episode_id, radarr_movie_id, "
                "       scan_id, source, subgen_version, queued_at, completed_at, "
                "       bazarr_scan_triggered_at "
                "FROM subs_generated "
                "WHERE completed_at IS NOT NULL "
                "  AND bazarr_scan_triggered_at IS NULL "
                "  AND series_id IS NOT NULL "
                "  AND completed_at >= ? "
                "ORDER BY completed_at DESC",
                (cutoff,),
            ).fetchall()
        return [LedgerEntry(*r) for r in rows]
