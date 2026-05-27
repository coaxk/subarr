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

    async def movies(self) -> list[dict[str, Any]]:
        return await self._get("/api/v3/movie")

    async def tags(self) -> list[dict[str, Any]]:
        return await self._get("/api/v3/tag")
