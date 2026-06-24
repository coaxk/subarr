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

    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        url = settings.sonarr_url if base_url is None else base_url
        key = settings.sonarr_api_key if api_key is None else api_key
        super().__init__(
            base_url=url if key else "",
            headers={"X-Api-Key": key} if key else None,
        )

    async def status(self) -> dict[str, Any]:
        return await self._get("/api/v3/system/status")

    async def root_folders(self) -> list[dict[str, Any]]:
        """GET /api/v3/rootfolder — the configured root folders (path,
        accessible, freeSpace). #134 Phase 0: the ground truth for the
        multi-library model — Phase 1 auto-derives `libraries[]` from these
        so multi-root onboarding stays zero-config."""
        return await self._get("/api/v3/rootfolder")

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

    async def languages(self) -> list[dict[str, Any]]:
        """Sonarr's language reference table. v1.1.1 #219: needed to map
        ISO codes (en, fr, ger) → Sonarr's numeric language IDs before we
        PUT episode-file language updates."""
        return await self._get("/api/v3/language")

    async def update_episode_file_languages(
        self,
        episode_file_id: int,
        languages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """v1.1.1 #219 audio-lang loop closer: PUT Sonarr's per-file
        language record so Bazarr's next sync sees the correct audio
        language. Bazarr then unblinds itself — episodes that were silently
        excluded from /wanted because file metadata claimed English get
        re-evaluated against the now-correct foreign-language audio.

        Uses /episodefile/bulk because the single-resource PUT
        /episodefile/{id} silently accepts the request but doesn't update
        the language field (Sonarr v4.0 behaviour confirmed against live
        instance 2026-05-31). The bulk endpoint accepts an array of
        {id, languages} and persists the change properly.

        `languages` is the Sonarr language list shape — each entry
        {id: int, name: str}."""
        return await self._put(
            "/api/v3/episodefile/bulk",
            json_body=[{"id": episode_file_id, "languages": languages}],
        )

    async def recent_imports_at(self, hours: int = 24) -> dict[int, float]:
        """v1.1-I / #117: sonarrEpisodeId → import epoch-seconds for files
        imported in the last <hours> hours. Walks /history?eventType=3
        (downloadFolderImported), newest-first, until past the cutoff. The
        timestamp powers the #117 settle-window; `recent_imports` derives the
        plain id set from this. On repeat imports of one episode we keep the
        most recent (records are date-descending, so first seen wins)."""
        import datetime

        cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=hours)
        out: dict[int, float] = {}
        page = 1
        while True:
            data = await self._get(
                "/api/v3/history",
                params={
                    "page": page,
                    "pageSize": 200,
                    "eventType": 3,  # downloadFolderImported
                    "sortKey": "date",
                    "sortDirection": "descending",
                },
            )
            records = data.get("records") or []
            crossed = False
            for r in records:
                date_s = r.get("date") or ""
                try:
                    dt = datetime.datetime.fromisoformat(date_s.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    continue
                if dt.replace(tzinfo=None) < cutoff:
                    crossed = True
                    break
                eid = r.get("episodeId")
                if isinstance(eid, int) and eid not in out:
                    out[eid] = dt.timestamp()  # tz-aware → correct UTC epoch
            if crossed or not records:
                break
            page += 1
            if page > 10:  # safety cap
                break
        return out

    async def recent_imports(self, hours: int = 24) -> set[int]:
        """v1.1-I: Episode IDs imported in the last <hours> hours."""
        return set((await self.recent_imports_at(hours)).keys())

    async def calendar_upcoming(self, hours: int = 48) -> set[int]:
        """v1.1-H: Episode IDs airing within the next <hours> hours.
        Returns set of sonarrEpisodeId. Pre-warms the queue so subs are
        ready when the episode imports."""
        import datetime

        start = datetime.datetime.utcnow()
        end = start + datetime.timedelta(hours=hours)
        rows = await self._get(
            "/api/v3/calendar",
            params={
                "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )
        return {r["id"] for r in rows if isinstance(r, dict) and "id" in r}

    async def wanted_missing_ids(self) -> set[int]:
        """v1.1-B: Sonarr's authoritative "no file yet" set, returned as
        a set of sonarrEpisodeId. Paginated endpoint — we walk pages until
        records run out. pageSize=1000 keeps it to 1-2 calls for typical
        libraries.

        Used by coverage_engine to split Bazarr-wanted rows into
        actionable (file exists) vs pending_download (file missing)."""
        ids: set[int] = set()
        page = 1
        while True:
            data = await self._get(
                "/api/v3/wanted/missing",
                params={
                    "page": page,
                    "pageSize": 1000,
                    "sortKey": "episodes.airDateUtc",
                    "sortDirection": "descending",
                },
            )
            records = data.get("records") or []
            for r in records:
                eid = r.get("id")
                if isinstance(eid, int):
                    ids.add(eid)
            total = data.get("totalRecords", 0)
            if page * 1000 >= total or not records:
                break
            page += 1
        return ids

    async def episodes(self, series_id: int) -> list[dict[str, Any]]:
        """All episodes for a series. Used to map sonarrEpisodeId → episodeFileId
        without per-episode requests."""
        return await self._get("/api/v3/episode", params={"seriesId": series_id})

    async def all_episode_files_with_mediainfo(
        self,
        series_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        """v1.1-A: Pull every episodefile row across the library, scoped
        by an optional series_ids filter. Each row contains the embedded
        `mediaInfo` block Sonarr populated on import — `audioLanguages`
        (e.g. 'eng') and `subtitles` (slash-delimited list, e.g.
        'eng/eng/ara/cze/...'). Lets us skip ffprobe on every file Sonarr
        has already mediainfo'd.

        Returns the raw episodefile shape — callers project to whatever
        they need. We don't filter null mediaInfo here; that decision
        belongs in arr_mediainfo.py.

        If series_ids is None, pulls for every series with episodeFileCount>0
        (cheaper than a wildcard query that returns empty for sparse libs).
        """
        import asyncio

        if series_ids is None:
            series = await self.series()
            series_ids = [
                s["id"] for s in series if (s.get("statistics") or {}).get("episodeFileCount", 0) > 0
            ]
        # 420 series × ~30 episodes serially = ~108s. Cap concurrency at 8
        # to be a polite client while still finishing in <15s.
        sem = asyncio.Semaphore(8)

        async def _one(sid: int) -> list[dict[str, Any]]:
            async with sem:
                try:
                    rows = await self.episode_files_for_series(sid)
                    for r in rows:
                        r.setdefault("seriesId", sid)
                    return rows
                except Exception:
                    return []

        chunks = await asyncio.gather(*(_one(sid) for sid in series_ids))
        out: list[dict[str, Any]] = []
        for c in chunks:
            out.extend(c)
        return out
