"""#71 -- the media-server abstraction. Plex, Jellyfin (slice 2), and Emby
(deferred) implement this. subarr writes subtitle sidecars to disk, then asks
every configured media server to pick them up; the protocol is that contract."""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)


@runtime_checkable
class MediaServer(Protocol):
    """Structural interface for a media server subarr can drive. `type` is a
    stable discriminator ("plex" | "jellyfin" | "emby")."""

    type: str

    def is_configured(self) -> bool: ...
    def translate_path(self, subarr_path: str) -> str: ...
    async def refresh_for_file(self, subarr_file: str) -> dict: ...
    async def full_refresh(self) -> dict: ...
    async def status(self) -> dict: ...
    async def audio_lang_hints(self, titles) -> dict: ...
    async def aclose(self) -> None: ...


async def refresh_file_on_all(servers, subarr_file: str) -> list[dict]:
    """Fan a per-file refresh out to every CONFIGURED server, best-effort: one
    server erroring or being unconfigured never blocks the others. Returns the
    per-server result dicts (skipped servers omitted)."""
    results: list[dict] = []
    for srv in servers:
        try:
            if not srv.is_configured():
                continue
            results.append(await srv.refresh_for_file(subarr_file))
        except Exception as e:  # noqa: BLE001 -- a failing server must not abort the loop
            log.warning("media-server refresh failed on %s: %s", getattr(srv, "type", "?"), e)
    return results
