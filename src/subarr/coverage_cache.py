"""v1.1 ARCH FIX: persistent + background-refreshed coverage snapshot.

Before this lived: every /api/coverage call rebuilt end-to-end (60-90s on
large libs hitting Bazarr+Sonarr+Radarr+Tautulli+Calendar+History +
probe reconciliation). The pending-review endpoint did the same. Pages
felt broken because every request blocked on a full rebuild.

After: one SQLite row holds the latest snapshot. A background task
rebuilds it every REFRESH_INTERVAL_S seconds. /api/coverage returns the
row instantly (sub-second). ?fresh=true blocks on a fresh build for the
caller who really wants it now. POST /api/coverage/refresh triggers a
background rebuild without blocking.

Snapshot table holds one row, replaced on each build. Schema deliberately
single-row keyed by id=1 — no history retention needed (we don't render
"snapshot 5 minutes ago" deltas yet; if we do later, this gets a
timestamp index).
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS coverage_snapshot (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    generated_at    REAL NOT NULL,
    items_json      TEXT NOT NULL,
    totals_json     TEXT NOT NULL,
    sources_json    TEXT NOT NULL,
    build_duration_s REAL,
    item_count      INTEGER
);
"""


@dataclass
class CachedSnapshot:
    generated_at: float
    items: list[dict[str, Any]]
    totals: dict[str, Any]
    sources: dict[str, Any]
    build_duration_s: float
    item_count: int

    def age_s(self) -> float:
        return time.time() - self.generated_at

    def to_response(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "items": self.items,
            "totals": self.totals,
            "sources": self.sources,
            "build_duration_s": self.build_duration_s,
            "cached": True,
            "cache_age_s": round(self.age_s(), 1),
        }


class CoverageCache:
    """Single-row SQLite snapshot + thread-safe in-memory mirror for
    fast reads. Writes happen in the background-refresh task; reads
    happen from any FastAPI worker."""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(db_path), check_same_thread=False, isolation_level=None,
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()
        self._cached: CachedSnapshot | None = None
        # Coordination flags: tells the refresh task whether a manual
        # refresh is in flight, and lets readers know if the snapshot
        # is currently being rebuilt.
        self._refresh_lock = asyncio.Lock()
        self._refreshing = False

    def init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
        # Warm the in-memory mirror from disk so the first request after
        # boot returns immediately (no cold start).
        self._load_from_db()

    def _load_from_db(self) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT generated_at, items_json, totals_json, sources_json, "
                "build_duration_s, item_count FROM coverage_snapshot WHERE id = 1"
            ).fetchone()
        if not row:
            return
        try:
            self._cached = CachedSnapshot(
                generated_at=row[0],
                items=json.loads(row[1]),
                totals=json.loads(row[2]),
                sources=json.loads(row[3]),
                build_duration_s=row[4] or 0.0,
                item_count=row[5] or 0,
            )
        except (ValueError, TypeError) as e:
            log.warning("coverage_snapshot row malformed; ignoring: %s", e)
            self._cached = None

    def get_cached(self) -> CachedSnapshot | None:
        return self._cached

    def is_refreshing(self) -> bool:
        return self._refreshing

    def store(self, *, items: list[dict[str, Any]], totals: dict[str, Any],
              sources: dict[str, Any], build_duration_s: float) -> CachedSnapshot:
        snap = CachedSnapshot(
            generated_at=time.time(),
            items=items, totals=totals, sources=sources,
            build_duration_s=build_duration_s,
            item_count=len(items),
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO coverage_snapshot "
                "(id, generated_at, items_json, totals_json, sources_json, build_duration_s, item_count) "
                "VALUES (1, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "  generated_at=excluded.generated_at, "
                "  items_json=excluded.items_json, "
                "  totals_json=excluded.totals_json, "
                "  sources_json=excluded.sources_json, "
                "  build_duration_s=excluded.build_duration_s, "
                "  item_count=excluded.item_count",
                (
                    snap.generated_at,
                    json.dumps(snap.items),
                    json.dumps(snap.totals),
                    json.dumps(snap.sources),
                    snap.build_duration_s,
                    snap.item_count,
                ),
            )
        self._cached = snap
        return snap

    async def refresh(self, bundle, probe_store, audio_lang_store,
                       use_tautulli: bool = True) -> CachedSnapshot:
        """Run build_coverage and store the result. Uses an asyncio.Lock
        so a parallel refresh waits for the in-flight one instead of
        duplicating the expensive build. Notifications dispatched after
        store so SSE consumers update."""
        async with self._refresh_lock:
            self._refreshing = True
            started = time.time()
            try:
                from .coverage_engine import build_coverage
                report = await build_coverage(
                    bundle, use_tautulli=use_tautulli,
                    probe_store=probe_store,
                    audio_lang_store=audio_lang_store,
                )
                body = report.to_dict()
                duration = time.time() - started
                snap = self.store(
                    items=body["items"],
                    totals=body["totals"],
                    sources=body["sources"],
                    build_duration_s=duration,
                )
                log.info("coverage cache refreshed in %.1fs (%d items)",
                         duration, snap.item_count)
                return snap
            finally:
                self._refreshing = False


# Background scheduler — fires refresh on a fixed interval. Started by
# the app lifespan; cancelled at shutdown. Independent of the existing
# coverage_walk schedule (which is user-configurable + much heavier);
# this one is purely the "keep the page snappy" tick.
DEFAULT_INTERVAL_S = 300  # 5 min


async def background_refresh_loop(
    cache: CoverageCache,
    bundle,
    probe_store,
    audio_lang_store,
    interval_s: int = DEFAULT_INTERVAL_S,
) -> None:
    """Sleep, refresh, repeat. Exits on cancellation."""
    # Initial refresh on boot if nothing cached yet — fills the snapshot
    # so the first /api/coverage request after a fresh deploy doesn't
    # block for 90s.
    if cache.get_cached() is None:
        log.info("coverage cache: no snapshot found; warming on boot")
        try:
            await cache.refresh(bundle, probe_store, audio_lang_store)
        except Exception as e:
            log.warning("coverage cache: initial warm failed: %s", e)
    while True:
        await asyncio.sleep(interval_s)
        try:
            await cache.refresh(bundle, probe_store, audio_lang_store)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("coverage cache: background refresh failed: %s", e)
