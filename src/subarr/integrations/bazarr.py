"""Bazarr API client (read-only for v1.1 batch 1).

Endpoints used:
- GET /api/system/status → version + uptime
- GET /api/badges → lightweight counts (episodes, movies, providers, status)
- GET /api/episodes/wanted → list of episodes with missing subs
- GET /api/movies/wanted → same for movies

Writes (POST /api/system/tasks, POST /api/episodes/subtitles) deferred to
v1.1 batch 2 when the queue-back-to-Bazarr flow lands.
"""
from __future__ import annotations

from typing import Any

from ..config import settings
from .base import IntegrationClient


class BazarrClient(IntegrationClient):
    name = "bazarr"

    def __init__(self):
        super().__init__(
            base_url=settings.bazarr_url if settings.bazarr_api_key else "",
            headers={"X-API-KEY": settings.bazarr_api_key} if settings.bazarr_api_key else None,
        )

    async def status(self) -> dict[str, Any]:
        d = await self._get("/api/system/status")
        # Bazarr wraps in {"data": {...}}; surface the inner dict directly.
        return d.get("data", d) if isinstance(d, dict) else d

    async def badges(self) -> dict[str, Any]:
        return await self._get("/api/badges")

    async def episodes_wanted(self) -> list[dict[str, Any]]:
        d = await self._get("/api/episodes/wanted")
        return d.get("data", []) if isinstance(d, dict) else []

    async def movies_wanted(self) -> list[dict[str, Any]]:
        d = await self._get("/api/movies/wanted")
        return d.get("data", []) if isinstance(d, dict) else []
