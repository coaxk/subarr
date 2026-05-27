"""Thin async client for the patched subgen HTTP API.

One client per app lifetime; reused across requests so connection pooling kicks
in (queue polling will hit /queue every couple of seconds from the Monitor tab).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import settings

log = logging.getLogger(__name__)

# 5s is generous for /queue / /status; /batch can take a while (it walks the folder
# tree synchronously inside subgen before returning the structured count). Pick
# something that won't time out on big folders but is short enough that a hung
# subgen surfaces quickly.
_DEFAULT_TIMEOUT = httpx.Timeout(connect=3.0, read=120.0, write=10.0, pool=3.0)


class SubgenUnavailable(RuntimeError):
    """Subgen container isn't reachable or returned an unparseable response."""


class SubgenClient:
    def __init__(self, base_url: str | None = None, timeout: httpx.Timeout | None = None):
        self._base_url = (base_url or settings.subgen_url).rstrip("/")
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout or _DEFAULT_TIMEOUT)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def queue(self) -> dict[str, Any]:
        try:
            r = await self._client.get("/queue")
        except httpx.HTTPError as e:
            raise SubgenUnavailable(f"subgen /queue failed: {e}") from e
        if r.status_code != 200:
            raise SubgenUnavailable(f"subgen /queue status {r.status_code}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError as e:
            raise SubgenUnavailable(f"subgen /queue returned non-json: {e}") from e

    async def status(self) -> dict[str, Any]:
        try:
            r = await self._client.get("/status")
        except httpx.HTTPError as e:
            raise SubgenUnavailable(f"subgen /status failed: {e}") from e
        if r.status_code != 200:
            raise SubgenUnavailable(f"subgen /status status {r.status_code}")
        return r.json()

    async def batch(self, directory: str, reverse: bool = False, force_language: str | None = None) -> tuple[int, dict[str, Any]]:
        """POST /batch with subgen's V4.1 structured response.

        Returns (status_code, body). Caller distinguishes:
          - 200 + walked > 0 => scan dispatched (counts in body)
          - 404 + walked == 0 => path resolved to no files
          - other => caller decides; we don't raise on 404
        """
        params: dict[str, Any] = {"directory": directory, "reverse": str(reverse).lower()}
        if force_language:
            params["forceLanguage"] = force_language
        try:
            r = await self._client.post("/batch", params=params)
        except httpx.HTTPError as e:
            raise SubgenUnavailable(f"subgen /batch failed: {e}") from e
        try:
            body = r.json()
        except ValueError:
            body = {"_raw": r.text[:500]}
        return r.status_code, body
