"""Plex Media Server client.

v1.1.1: Partial-scan support. After subarr (or subsyncarr) writes a subtitle
sidecar next to a video file, we fire a partial-scan against Plex pointed
at that file's directory so Plex picks up the sidecar instantly — instead
of waiting for its next periodic library scan (often hours).

This is the keystone fix for the Apple TV side quest: on Apple TV, Plex
won't let users upload a sub via the player. The only way fresh sidecars
appear on Apple TV is if Plex *already knows about them*. Subarr writing
the sidecar isn't enough on its own — Plex has to scan it. We trigger the
scan; user opens Apple TV, sub is there.

Plex endpoint shape:
    GET /library/sections/{section_id}/refresh?path=<encoded_dir>&X-Plex-Token=<t>

- section_id MUST be numeric — "all" only works for full scans.
- path MUST be the directory Plex sees the file in. If Plex and subarr
  mount the media tree at different paths in their respective containers,
  set PLEX_PATH_PREFIX to translate (e.g. subarr sees /media/library,
  Plex sees /data/Media → PLEX_PATH_PREFIX=/data/Media). When unset we
  assume the two are equal.
- Response is 200 with an empty / minimal XML body. Plex queues the scan
  asynchronously — there is no completion signal. Best-effort fire-and-forget.

We discover the right section id at runtime by listing /library/sections
and matching the path against each section's <Location> entries. Cached
per-process; if Plex sections change subarr restart picks them up.
"""
from __future__ import annotations

import logging
from pathlib import PurePosixPath
from typing import Optional
# defusedxml hardens against billion-laughs / external-entity attacks.
# Plex is a trusted local service but a compromised Plex container
# should not be a foothold into subarr — defusedxml is a 1-line swap.
import defusedxml.ElementTree as ET  # noqa: N814

import httpx

from . import IntegrationError

log = logging.getLogger(__name__)


class PlexClient:
    """Minimal Plex client. Not modelled on IntegrationClient because Plex
    returns XML (not JSON) and uses query-string auth rather than headers."""

    name = "plex"

    def __init__(self, base_url: str, token: str, default_section: str = "all",
                 path_prefix: str = "", media_root: str = ""):
        self._base_url = (base_url or "").rstrip("/")
        self._token = token or ""
        self._default_section = default_section or "all"
        # PLEX_PATH_PREFIX: what Plex sees the media tree as. If empty we
        # assume Plex shares subarr's view (media_root). Common case in
        # docker-compose where both containers bind-mount the same host
        # path to identical container paths.
        self._path_prefix = (path_prefix or "").rstrip("/")
        self._media_root = (media_root or "").rstrip("/")
        # Discovered section list — list[dict(id, paths=[str,...])].
        # Lazily fetched on first partial_scan(); refreshed only on restart.
        self._sections: list[dict] | None = None
        # #12 audio-hint caches. _show_index: title_lc → show ratingKey
        # (built once). _audio_hint_cache: title_lc → lang3 | None (None =
        # resolved-but-no-pick, so we don't re-query). Both cleared only on
        # restart — per-show audio prefs are stable.
        self._show_index: dict[str, str] | None = None
        self._audio_hint_cache: dict[str, str | None] = {}

    def is_configured(self) -> bool:
        return bool(self._base_url and self._token)

    def translate_path(self, subarr_path: str) -> str:
        """Translate a subarr-side absolute path (e.g. /media/library/TV/X.srt)
        into the path Plex sees. Identity translation when PLEX_PATH_PREFIX
        is unset or matches subarr's media_root."""
        if not self._path_prefix or not self._media_root:
            return subarr_path
        if subarr_path.startswith(self._media_root):
            return self._path_prefix + subarr_path[len(self._media_root):]
        # Unknown root — return as-is and let Plex's section matcher decide.
        return subarr_path

    async def _list_sections(self) -> list[dict]:
        """Fetch + parse /library/sections. Returns list of
        {'id': str, 'title': str, 'paths': [str,...]}."""
        url = f"{self._base_url}/library/sections"
        params = {"X-Plex-Token": self._token}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(url, params=params)
        except httpx.HTTPError as e:
            raise IntegrationError(f"plex sections: {e}") from e
        if r.status_code >= 400:
            raise IntegrationError(
                f"plex sections HTTP {r.status_code}: {r.text[:200]}"
            )
        try:
            root = ET.fromstring(r.text)
        except ET.ParseError as e:
            raise IntegrationError(f"plex sections: bad XML ({e})") from e
        out = []
        for directory in root.iter("Directory"):
            sid = directory.get("key")
            if not sid:
                continue
            title = directory.get("title") or ""
            paths = [loc.get("path") for loc in directory.iter("Location")
                     if loc.get("path")]
            out.append({"id": sid, "title": title, "paths": paths})
        return out

    async def sections(self, refresh: bool = False) -> list[dict]:
        if self._sections is None or refresh:
            self._sections = await self._list_sections()
        return self._sections

    async def _section_for_path(self, plex_path: str) -> Optional[str]:
        """Find the section whose Location is a prefix of plex_path.
        Returns the numeric section id as str, or None."""
        try:
            sects = await self.sections()
        except IntegrationError as e:
            log.warning("plex section discovery failed: %s", e)
            return None
        # Longest-prefix wins (handles nested libraries).
        best = None
        best_len = -1
        target = PurePosixPath(plex_path)
        for s in sects:
            for loc in s.get("paths", []):
                try:
                    p = PurePosixPath(loc)
                except (TypeError, ValueError):
                    continue
                # str-prefix match because Plex Location.path is an exact
                # directory and target is a file beneath it.
                loc_str = str(p).rstrip("/")
                t_str = str(target).rstrip("/")
                if t_str == loc_str or t_str.startswith(loc_str + "/"):
                    if len(loc_str) > best_len:
                        best = s["id"]
                        best_len = len(loc_str)
        return best

    async def full_scan(self, section_id: Optional[str] = None) -> dict:
        """Trigger a full scan against the configured (or supplied) section.
        Used by /api/plex/scan as the public 'rescan everything' button."""
        if not self.is_configured():
            raise IntegrationError("plex not configured")
        sid = section_id or self._default_section
        url = f"{self._base_url}/library/sections/{sid}/refresh"
        params = {"X-Plex-Token": self._token}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(url, params=params)
        except httpx.HTTPError as e:
            raise IntegrationError(f"plex full_scan: {e}") from e
        if r.status_code >= 400:
            raise IntegrationError(
                f"plex full_scan HTTP {r.status_code}: {r.text[:200]}"
            )
        return {"triggered": True, "section": sid, "scope": "full",
                "plex_status": r.status_code}

    async def partial_scan(self, subarr_file_path: str) -> dict:
        """Fire a path-scoped refresh covering the directory containing
        `subarr_file_path` (a path on subarr's filesystem view). Best-effort:
        returns dict with `triggered`, `section`, `scope`, `plex_path`.

        Path is translated via PLEX_PATH_PREFIX if configured. Section is
        discovered automatically when PLEX_SECTION is "all"."""
        if not self.is_configured():
            raise IntegrationError("plex not configured")
        plex_path = self.translate_path(subarr_file_path)
        # Plex expects a directory; pass the parent of the file.
        scan_dir = str(PurePosixPath(plex_path).parent)
        # Resolve section: explicit numeric setting wins, else discover.
        sid: Optional[str] = None
        if self._default_section and self._default_section != "all":
            try:
                int(self._default_section)
                sid = self._default_section
            except (TypeError, ValueError):
                pass
        if sid is None:
            sid = await self._section_for_path(scan_dir)
        if sid is None:
            raise IntegrationError(
                f"plex partial_scan: no section matched path {scan_dir!r}; "
                f"set PLEX_SECTION to the numeric id or check PLEX_PATH_PREFIX"
            )
        url = f"{self._base_url}/library/sections/{sid}/refresh"
        params = {"X-Plex-Token": self._token, "path": scan_dir}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(url, params=params)
        except httpx.HTTPError as e:
            raise IntegrationError(f"plex partial_scan: {e}") from e
        if r.status_code >= 400:
            raise IntegrationError(
                f"plex partial_scan HTTP {r.status_code}: {r.text[:200]}"
            )
        log.info("plex partial_scan: section=%s path=%s", sid, scan_dir)
        return {"triggered": True, "section": sid, "scope": "partial",
                "plex_path": scan_dir, "plex_status": r.status_code}

    async def status(self) -> dict:
        """Liveness + version probe for /api/integrations/health. Hits Plex
        /identity (cheap — no library scan). Raises IntegrationError on
        failure so the health router marks Plex offline."""
        root = await self._get_xml("/identity")
        return {
            "version": root.get("version"),
            "machine_id": root.get("machineIdentifier"),
        }

    # ── #12: per-show selected audio language ───────────────────────────

    async def _get_xml(self, path: str, extra_params: dict | None = None):
        """GET a Plex endpoint and return the parsed XML root."""
        params = {"X-Plex-Token": self._token, **(extra_params or {})}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"{self._base_url}{path}", params=params)
        except httpx.HTTPError as e:
            raise IntegrationError(f"plex {path}: {e}") from e
        if r.status_code >= 400:
            raise IntegrationError(f"plex {path} HTTP {r.status_code}")
        try:
            return ET.fromstring(r.text)
        except ET.ParseError as e:
            raise IntegrationError(f"plex {path}: bad XML ({e})") from e

    async def _ensure_show_index(self) -> None:
        """Build title_lc → show ratingKey across all show-type sections.
        One listing call per show section; cached for the process lifetime."""
        if self._show_index is not None:
            return
        idx: dict[str, str] = {}
        root = await self._get_xml("/library/sections")
        show_section_ids = [
            d.get("key") for d in root.iter("Directory")
            if d.get("type") == "show" and d.get("key")
        ]
        for sid in show_section_ids:
            try:
                sroot = await self._get_xml(f"/library/sections/{sid}/all", {"type": "2"})
            except IntegrationError as e:
                log.debug("plex show listing failed for section %s: %s", sid, e)
                continue
            for d in sroot.iter("Directory"):
                t = (d.get("title") or "").strip().lower()
                rk = d.get("ratingKey")
                if t and rk and t not in idx:
                    idx[t] = rk
        self._show_index = idx

    async def _show_selected_audio(self, show_key: str) -> str | None:
        """Read the selected audio stream's language for a show, by sampling
        its first episode (Plex exposes Stream selection only per-item).
        Returns a 3-letter language code (e.g. 'dan') or None."""
        leaves = await self._get_xml(f"/library/metadata/{show_key}/allLeaves")
        ep = next(leaves.iter("Video"), None)
        ep_key = ep.get("ratingKey") if ep is not None else None
        if not ep_key:
            return None
        meta = await self._get_xml(f"/library/metadata/{ep_key}")
        for s in meta.iter("Stream"):
            if s.get("streamType") == "2" and s.get("selected") == "1":
                code = (s.get("languageCode") or "").strip().lower()
                return code or None
        return None

    async def audio_lang_hints(self, titles) -> dict[str, str]:
        """Return {title_lc: lang3} of the user's per-show selected audio for
        the given show titles, read from Plex. Cached per-title across calls
        (incl. negative results) so steady-state builds add no Plex I/O.
        Best-effort: a lookup failure yields no hint for that title rather
        than raising — never breaks a coverage build."""
        if not self.is_configured():
            return {}
        wanted = {(t or "").strip().lower() for t in titles if t and t.strip()}
        unresolved = wanted - set(self._audio_hint_cache)
        if unresolved:
            try:
                await self._ensure_show_index()
            except IntegrationError as e:
                log.warning("plex audio hints: show index failed: %s", e)
                # Mark as resolved-empty so we don't hammer Plex every build.
                for tl in unresolved:
                    self._audio_hint_cache.setdefault(tl, None)
                self._show_index = self._show_index or {}
            for tl in unresolved:
                key = (self._show_index or {}).get(tl)
                lang: str | None = None
                if key:
                    try:
                        lang = await self._show_selected_audio(key)
                    except IntegrationError as e:
                        log.debug("plex audio hint failed for %r: %s", tl, e)
                self._audio_hint_cache[tl] = lang
        return {tl: self._audio_hint_cache[tl]
                for tl in wanted
                if self._audio_hint_cache.get(tl)}

    async def aclose(self) -> None:
        # Stateless — each call opens its own client. Nothing to close.
        return None
