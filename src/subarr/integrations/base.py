"""Shared HTTPX session for all integration clients."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from . import IntegrationError

log = logging.getLogger(__name__)

# Most arr/bazarr/tautulli requests return in <2s, BUT /api/v3/series with
# 1500+ entries serialises to ~5MB and takes 30-60s on a busy Sonarr. Read
# timeout is sized for the heaviest known endpoint with headroom. Connect
# stays tight so a downed upstream surfaces fast.
_DEFAULT_TIMEOUT = httpx.Timeout(connect=3.0, read=90.0, write=10.0, pool=3.0)


class IntegrationClient:
    """Base class. Holds an httpx.AsyncClient bound to a base_url."""

    name: str = "integration"

    def __init__(self, base_url: str, headers: dict[str, str] | None = None,
                 timeout: httpx.Timeout | None = None):
        self._base_url = base_url.rstrip("/") if base_url else ""
        self._configured = bool(base_url)
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout or _DEFAULT_TIMEOUT,
            headers=headers or {},
        )

    def is_configured(self) -> bool:
        return self._configured

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict | None = None) -> Any:
        if not self._configured:
            raise IntegrationError(f"{self.name}: not configured (URL or API key missing)")
        try:
            r = await self._client.get(path, params=params)
        except httpx.HTTPError as e:
            raise IntegrationError(f"{self.name} {path}: {e}") from e
        if r.status_code >= 400:
            raise IntegrationError(f"{self.name} {path}: HTTP {r.status_code}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError as e:
            raise IntegrationError(f"{self.name} {path}: non-json response") from e

    async def _post(self, path: str, params: dict | None = None, json_body: dict | None = None) -> Any:
        if not self._configured:
            raise IntegrationError(f"{self.name}: not configured")
        try:
            r = await self._client.post(path, params=params, json=json_body)
        except httpx.HTTPError as e:
            raise IntegrationError(f"{self.name} {path}: {e}") from e
        if r.status_code >= 400:
            raise IntegrationError(f"{self.name} {path}: HTTP {r.status_code}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError:
            return None

    async def _put(self, path: str, params: dict | None = None, json_body: dict | None = None) -> Any:
        """PUT — used by v1.1.1 audio-lang propagation to Sonarr's
        episodeFile resource. Same error-translation contract as _post."""
        if not self._configured:
            raise IntegrationError(f"{self.name}: not configured")
        try:
            r = await self._client.put(path, params=params, json=json_body)
        except httpx.HTTPError as e:
            raise IntegrationError(f"{self.name} {path}: {e}") from e
        if r.status_code >= 400:
            raise IntegrationError(f"{self.name} {path}: HTTP {r.status_code}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError:
            return None
