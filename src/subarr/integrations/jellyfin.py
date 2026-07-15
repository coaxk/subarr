"""#71/#72 — Jellyfin backend for the MediaServer abstraction.

subarr writes subtitle sidecars to disk; this asks Jellyfin to pick them up.
Jellyfin has no path-based refresh (unlike Plex) and works by item UUID, and
`/Items` has no server-side path filter — so we cache a Path→itemId index
(one `/Items?fields=Path` query, lazily built + rebuilt on miss) and
`POST /Items/{id}/Refresh`. Auth is the `X-Emby-Token` header. Validated live
against Jellyfin 10.11.11."""

from __future__ import annotations

import logging

import httpx

from ..circuit_breaker import CircuitBreaker
from . import IntegrationError
from .base import _DEFAULT_TIMEOUT, _is_client_closed

log = logging.getLogger(__name__)


class JellyfinClient:
    name = "jellyfin"
    type = "jellyfin"

    def __init__(
        self, base_url: str, api_key: str, path_prefix: str = "", media_root: str = "", breaker=None
    ):
        self._base_url = (base_url or "").rstrip("/")
        self._api_key = api_key or ""
        self._path_prefix = (path_prefix or "").rstrip("/")
        self._media_root = (media_root or "").rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"X-Emby-Token": self._api_key} if self._api_key else {},
            timeout=_DEFAULT_TIMEOUT,
        )
        self._breaker = breaker or CircuitBreaker(name=self.name)
        self._path_index: dict[str, str] | None = None  # jellyfin Path -> itemId
        self._auto_prefix: str | None = None  # cached auto-detected prefix ("" = none found)

    def is_configured(self) -> bool:
        return bool(self._base_url and self._api_key)

    def translate_path(self, subarr_path: str, prefix: str | None = None) -> str:
        p = self._path_prefix if prefix is None else prefix
        if not p or not self._media_root:
            return subarr_path
        if subarr_path.startswith(self._media_root):
            return p + subarr_path[len(self._media_root) :]
        return subarr_path

    async def _request(self, method: str, path: str, params: dict | None = None) -> httpx.Response:
        """GET/POST via the persistent client with breaker guard. Mirrors
        PlexClient._request policy (5xx/transport → failure; 4xx reachable but
        raises; client-closed race degrades cleanly)."""
        if not self._breaker.allow():
            raise IntegrationError(f"{self.name}: circuit open — Jellyfin failing, backing off")
        try:
            r = await self._client.request(method, path, params=params)
        except httpx.HTTPError as e:
            self._breaker.record_failure()
            raise IntegrationError(f"{self.name} {path}: {e}") from e
        except RuntimeError as e:
            if not _is_client_closed(e):
                raise
            raise IntegrationError(f"{self.name} {path}: client closed mid-request") from e
        if r.status_code >= 500:
            self._breaker.record_failure()
        else:
            self._breaker.record_success()
        if r.status_code >= 400:
            raise IntegrationError(f"{self.name} {path}: HTTP {r.status_code}: {r.text[:200]}")
        return r

    async def _build_path_index(self) -> dict[str, str]:
        r = await self._request(
            "GET",
            "/Items",
            params={
                "recursive": "true",
                "includeItemTypes": "Episode,Movie",
                "fields": "Path",
                "enableTotalRecordCount": "false",
            },
        )
        items = r.json().get("Items", [])
        return {it["Path"]: it["Id"] for it in items if it.get("Path") and it.get("Id")}

    async def _find_item_id(self, jf_path: str) -> str | None:
        if self._path_index is None:
            self._path_index = await self._build_path_index()
        item_id = self._path_index.get(jf_path)
        if item_id is None:
            self._path_index = await self._build_path_index()  # maybe newly added
            item_id = self._path_index.get(jf_path)
        return item_id

    async def library_locations(self) -> list[str]:
        libs = await self.libraries()
        return [p for lib in libs for p in (lib.get("paths") or [])]

    async def _effective_prefix(self, sample_subarr_path: str) -> str:
        """Explicit JELLYFIN_PATH_PREFIX wins; else derive from library locations
        (cached); else identity. Cached "" means 'derived nothing, use identity'."""
        if self._path_prefix:
            return self._path_prefix
        if self._auto_prefix is not None:
            return self._auto_prefix
        from .media_server import derive_path_prefix

        derived = None
        try:
            derived = derive_path_prefix(await self.library_locations(), self._media_root, sample_subarr_path)
        except Exception as e:  # noqa: BLE001
            log.warning("jellyfin: path-prefix auto-detect failed: %s", e)
        self._auto_prefix = derived or ""
        log.info(
            "jellyfin: path-prefix %s (media_root=%s)",
            f"auto-detected {derived!r}" if derived else "auto-detect found none; using identity",
            self._media_root,
        )
        return self._auto_prefix

    async def refresh_for_file(self, subarr_file: str) -> dict:
        prefix = await self._effective_prefix(subarr_file)
        jf_path = self.translate_path(subarr_file, prefix)
        item_id = await self._find_item_id(jf_path)
        if item_id is None:
            log.warning("jellyfin: no item matched %s (path-prefix mismatch or not indexed)", jf_path)
            return {"triggered": False, "reason": "no_item_match", "path": jf_path}
        await self._request("POST", f"/Items/{item_id}/Refresh", params={"metadataRefreshMode": "Default"})
        log.info("jellyfin: refreshed item %s for %s", item_id, jf_path)
        return {"triggered": True, "scope": "item", "item_id": item_id, "path": jf_path}

    async def full_refresh(self) -> dict:
        await self._request("POST", "/Library/Refresh")
        return {"triggered": True, "scope": "full"}

    async def status(self) -> dict:
        r = await self._request("GET", "/System/Info/Public")
        d = r.json()
        return {"version": d.get("Version"), "server_name": d.get("ServerName")}

    async def libraries(self) -> list[dict]:
        r = await self._request("GET", "/Library/VirtualFolders")
        return [{"name": v.get("Name"), "paths": v.get("Locations", [])} for v in r.json()]

    async def audio_lang_hints(self, titles) -> dict:
        # Deferred: Jellyfin audio-track hints land in a later slice. Returning
        # {} keeps the protocol satisfied without adding N-query cost now.
        return {}

    async def aclose(self) -> None:
        await self._client.aclose()
