"""Sonarr v3 API client (read-only).

Endpoints used:
- GET /api/v3/system/status → version
- GET /api/v3/series → master list (id, title, tvdbId, monitored, originalLanguage, path, tags)
- GET /api/v3/tag → tag id→label map (we want labels in coverage output)
"""
from __future__ import annotations

from typing import Any

from ..config import settings
from .base import IntegrationClient


class SonarrClient(IntegrationClient):
    name = "sonarr"

    def __init__(self):
        super().__init__(
            base_url=settings.sonarr_url if settings.sonarr_api_key else "",
            headers={"X-Api-Key": settings.sonarr_api_key} if settings.sonarr_api_key else None,
        )

    async def status(self) -> dict[str, Any]:
        return await self._get("/api/v3/system/status")

    async def series(self) -> list[dict[str, Any]]:
        return await self._get("/api/v3/series")

    async def tags(self) -> list[dict[str, Any]]:
        return await self._get("/api/v3/tag")

    async def episode(self, episode_id: int) -> dict[str, Any]:
        return await self._get(f"/api/v3/episode/{episode_id}")

    async def episode_file(self, episode_file_id: int) -> dict[str, Any]:
        """Per-file info: includes `path` — the absolute path Sonarr stores."""
        return await self._get(f"/api/v3/episodefile/{episode_file_id}")

    async def episode_files_for_series(self, series_id: int) -> list[dict[str, Any]]:
        """All episode-file rows for a series in one call. Used by the coverage
        engine to do authoritative stale-disk detection without 1-call-per-ep —
        we map episodeFileId → path locally."""
        return await self._get("/api/v3/episodefile", params={"seriesId": series_id})

    async def episodes(self, series_id: int) -> list[dict[str, Any]]:
        """All episodes for a series. Used to map sonarrEpisodeId → episodeFileId
        without per-episode requests."""
        return await self._get("/api/v3/episode", params={"seriesId": series_id})
