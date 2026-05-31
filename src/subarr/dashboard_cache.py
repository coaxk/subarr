"""v1.1 ARCH: in-memory cache for /api/home/dashboard.

Same pattern as coverage_cache but lighter — dashboard data is ephemeral
(GPU snapshots, in-flight queue counts, badges) so we keep it in RAM only
and refresh aggressively. 30-second tick by default; survives requests
but not container restarts (one cold build on boot, then cached).

Bottleneck the page is fixing: Bazarr's wanted lists (eps + movs) +
Sonarr/Radarr status + Tautulli + Ollama + GPU + provenance all run
in parallel inside _build_dashboard(), but the SLOWEST of the parallel
calls (usually Bazarr wanted on large libs) gates the response. With this
cache, the user pays that latency once per 30s background tick instead
of once per page load.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class DashboardSnapshot:
    generated_at: float
    payload: dict[str, Any]
    build_duration_s: float

    def age_s(self) -> float:
        return time.time() - self.generated_at

    def to_response(self) -> dict[str, Any]:
        out = dict(self.payload)
        out["cached"] = True
        out["cache_age_s"] = round(self.age_s(), 1)
        out["build_duration_s"] = round(self.build_duration_s, 2)
        return out


class DashboardCache:
    def __init__(self):
        self._snapshot: DashboardSnapshot | None = None
        self._lock = asyncio.Lock()
        self._refreshing = False

    def get_cached(self) -> DashboardSnapshot | None:
        return self._snapshot

    def is_refreshing(self) -> bool:
        return self._refreshing

    async def refresh(self, build_fn) -> DashboardSnapshot:
        """build_fn must be an async callable that returns the dashboard
        payload dict (same shape as the live endpoint)."""
        async with self._lock:
            self._refreshing = True
            started = time.time()
            try:
                payload = await build_fn()
                duration = time.time() - started
                snap = DashboardSnapshot(
                    generated_at=time.time(),
                    payload=payload,
                    build_duration_s=duration,
                )
                self._snapshot = snap
                log.info("dashboard cache refreshed in %.1fs", duration)
                return snap
            finally:
                self._refreshing = False


DEFAULT_INTERVAL_S = 30


async def background_refresh_loop(cache: DashboardCache, build_fn,
                                   interval_s: int = DEFAULT_INTERVAL_S) -> None:
    # Warm on boot if empty.
    if cache.get_cached() is None:
        try:
            await cache.refresh(build_fn)
        except Exception as e:
            log.warning("dashboard cache: initial warm failed: %s", e)
    while True:
        await asyncio.sleep(interval_s)
        try:
            await cache.refresh(build_fn)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("dashboard cache: background refresh failed: %s", e)
