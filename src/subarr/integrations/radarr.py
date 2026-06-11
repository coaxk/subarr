"""Radarr v3 API client (read-only).

Mirror of Sonarr's structure. Same v3 contract, same auth header.
"""

from __future__ import annotations

from typing import Any

from ..config import settings
from .base import IntegrationClient


class RadarrClient(IntegrationClient):
    name = "radarr"

    def __init__(self):
        super().__init__(
            base_url=settings.radarr_url if settings.radarr_api_key else "",
            headers={"X-Api-Key": settings.radarr_api_key} if settings.radarr_api_key else None,
        )

    async def status(self) -> dict[str, Any]:
        return await self._get("/api/v3/system/status")

    async def root_folders(self) -> list[dict[str, Any]]:
        """GET /api/v3/rootfolder — the configured root folders (path,
        accessible, freeSpace). #134 Phase 0: the ground truth for the
        multi-library model — Phase 1 auto-derives `libraries[]` from these
        so multi-root onboarding stays zero-config."""
        return await self._get("/api/v3/rootfolder")

    async def movies(self) -> list[dict[str, Any]]:
        return await self._get("/api/v3/movie")

    async def tags(self) -> list[dict[str, Any]]:
        return await self._get("/api/v3/tag")

    async def recent_imports_at(self, hours: int = 24) -> dict[int, float]:
        """v1.1-I / #117: movieId → import epoch-seconds for files imported in
        the last <hours> hours via /history?eventType=3 (newest-first). The
        timestamp powers the #117 settle-window; `recent_imports` derives the
        plain id set. Repeat imports of one movie keep the most recent."""
        import datetime

        cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=hours)
        out: dict[int, float] = {}
        page = 1
        while True:
            data = await self._get(
                "/api/v3/history",
                params={
                    "page": page,
                    "pageSize": 200,
                    "eventType": 3,
                    "sortKey": "date",
                    "sortDirection": "descending",
                },
            )
            records = data.get("records") or []
            crossed = False
            for r in records:
                date_s = r.get("date") or ""
                try:
                    dt = datetime.datetime.fromisoformat(date_s.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    continue
                if dt.replace(tzinfo=None) < cutoff:
                    crossed = True
                    break
                mid = r.get("movieId")
                if isinstance(mid, int) and mid not in out:
                    out[mid] = dt.timestamp()  # tz-aware → correct UTC epoch
            if crossed or not records:
                break
            page += 1
            if page > 10:
                break
        return out

    async def recent_imports(self, hours: int = 24) -> set[int]:
        """v1.1-I: movieIds imported in the last <hours> hours."""
        return set((await self.recent_imports_at(hours)).keys())

    async def wanted_missing_ids(self) -> set[int]:
        """v1.1-B: Radarr's authoritative "no file yet" set as radarrMovieId."""
        ids: set[int] = set()
        page = 1
        while True:
            data = await self._get(
                "/api/v3/wanted/missing",
                params={
                    "page": page,
                    "pageSize": 1000,
                    "sortKey": "movies.sortTitle",
                    "sortDirection": "ascending",
                },
            )
            records = data.get("records") or []
            for r in records:
                mid = r.get("id")
                if isinstance(mid, int):
                    ids.add(mid)
            total = data.get("totalRecords", 0)
            if page * 1000 >= total or not records:
                break
            page += 1
        return ids

    async def movie_files_for_movie(self, movie_id: int) -> list[dict[str, Any]]:
        """Per-movie file list. Returns [] if the movie has no file."""
        return await self._get("/api/v3/moviefile", params={"movieId": movie_id})

    async def all_movie_files_with_mediainfo(self) -> list[dict[str, Any]]:
        """v1.1-A: Pull every moviefile across the library via the /movie
        list → /moviefile?movieId= fan-out. Radarr (unlike Sonarr) requires
        a movieId filter on /moviefile, so we paginate by movie.

        Parallelism is capped at 8 to avoid hammering Radarr while still
        making the fan-out 8× faster than serial. Each row's mediaInfo
        block has the same shape as Sonarr's."""
        import asyncio

        movies = await self.movies()
        ids = [m["id"] for m in movies if (m.get("hasFile") or m.get("movieFile"))]
        sem = asyncio.Semaphore(8)

        async def _one(mid: int) -> list[dict[str, Any]]:
            async with sem:
                try:
                    return await self.movie_files_for_movie(mid)
                except Exception:
                    return []

        results = await asyncio.gather(*(_one(mid) for mid in ids))
        out: list[dict[str, Any]] = []
        for chunk in results:
            for r in chunk:
                r.setdefault("movieId", r.get("movieId"))
                out.extend([r])
        return out
