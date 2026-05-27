"""Tautulli API client (read-only).

Tautulli uses a single endpoint /api/v2 with cmd= query param. Returns
{response: {result, data: ...}} where the inner data shape depends on cmd.

We use:
- cmd=status → trivial health check
- cmd=get_history → episode + movie playback history with rating_keys to
  cross-reference with Plex / Sonarr / Radarr / Bazarr.
"""
from __future__ import annotations

from typing import Any

from ..config import settings
from .base import IntegrationClient
from . import IntegrationError


class TautulliClient(IntegrationClient):
    name = "tautulli"

    def __init__(self):
        super().__init__(
            base_url=settings.tautulli_url if settings.tautulli_api_key else "",
        )
        self._apikey = settings.tautulli_api_key

    async def _cmd(self, cmd: str, **params) -> Any:
        if not self._configured:
            raise IntegrationError("tautulli: not configured")
        full = {"apikey": self._apikey, "cmd": cmd, **params}
        d = await self._get("/api/v2", params=full)
        resp = (d or {}).get("response") or {}
        if resp.get("result") != "success":
            raise IntegrationError(f"tautulli {cmd}: {resp.get('message') or 'no result'}")
        return resp.get("data")

    async def status(self) -> dict[str, Any]:
        # cmd=status returns {response:{result:success, data:null}} when up.
        await self._cmd("status")
        return {"result": "success"}

    async def history(self, length: int = 200, days: int | None = 30) -> list[dict[str, Any]]:
        """Return playback history rows (newest first).

        `length` caps the number of rows. `days` filters to recent activity
        when set; Tautulli's `start_date` accepts unix epoch seconds.
        """
        params: dict[str, Any] = {"length": length}
        if days is not None:
            import time
            params["start_date"] = int(time.time() - days * 86400)
        d = await self._cmd("get_history", **params)
        if isinstance(d, dict):
            return d.get("data", []) or []
        return []
