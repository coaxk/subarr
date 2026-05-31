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
        when set; Tautulli's `start_date` must be YYYY-MM-DD format in
        practice — the docs claim epoch is supported but it silently returns
        0 rows when an epoch is passed (verified against tautulli 2.15+).
        """
        params: dict[str, Any] = {"length": length}
        if days is not None:
            import datetime
            cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
            params["start_date"] = cutoff.strftime("%Y-%m-%d")
        d = await self._cmd("get_history", **params)
        if isinstance(d, dict):
            return d.get("data", []) or []
        return []

    async def get_users(self) -> list[dict[str, Any]]:
        """v1.1-L: list of Plex users known to Tautulli. Used to build
        per-user language profiles."""
        d = await self._cmd("get_users")
        if isinstance(d, list):
            return d
        if isinstance(d, dict):
            return d.get("data", []) or []
        return []

    async def get_user_watch_time_stats(
        self, user_id: int, query_days: int = 30,
    ) -> list[dict[str, Any]]:
        """Per-user watch stats — total plays/duration over a window."""
        d = await self._cmd("get_user_watch_time_stats",
                            user_id=user_id, query_days=query_days)
        if isinstance(d, list):
            return d
        return d.get("data", []) if isinstance(d, dict) else []

    async def get_activity(self) -> dict[str, Any]:
        """v1.1-E: Tautulli's currently-streaming sessions.

        Returns the raw payload — typically `{stream_count, sessions: [...]}`.
        Each session row carries `grandparent_title` (series), `parent_title`
        (season), `title` (episode), `rating_key` (Plex), `user`,
        `subtitle_decision` ('transcode'/'direct play'/'copy'),
        `subtitle_codec`, `subtitle_language_code`, and `progress_percent`.

        Most actionable signal: `subtitle_decision='transcode'` means Plex
        is burning CPU because no proper sidecar exists — perfect target
        for an instant subgen queue."""
        d = await self._cmd("get_activity")
        return d if isinstance(d, dict) else {}
