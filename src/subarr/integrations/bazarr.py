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

    async def episodes_history(self, sonarr_episode_id: int | None = None,
                                length: int = 50) -> list[dict[str, Any]]:
        """Per-episode subtitle download history (provider, score, timestamp).
        If sonarr_episode_id supplied, Bazarr returns rows for that episode."""
        params: dict[str, Any] = {"length": length}
        if sonarr_episode_id is not None:
            params["sonarrEpisodeId"] = sonarr_episode_id
        d = await self._get("/api/episodes/history", params=params)
        return d.get("data", []) if isinstance(d, dict) else []

    async def movies_history(self, radarr_movie_id: int | None = None,
                              length: int = 50) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"length": length}
        if radarr_movie_id is not None:
            params["radarrId"] = radarr_movie_id
        d = await self._get("/api/movies/history", params=params)
        return d.get("data", []) if isinstance(d, dict) else []

    async def blacklist_episode(
        self, *, series_id: int, episode_id: int, provider: str,
        subs_id: str, language: str, subtitles_path: str,
    ) -> dict[str, Any] | None:
        """v1.1-N: POST /api/episodes/blacklist — mark a downloaded sub
        as bad so Bazarr stops refetching the same broken release. Called
        by subarr's completion-watcher when ffprobe detects audio↔sub
        sync-offset > 1s on a Bazarr-sourced sub."""
        data = {
            "seriesid": str(series_id),
            "episodeid": str(episode_id),
            "provider": provider,
            "subs_id": subs_id,
            "language": language,
            "subtitles_path": subtitles_path,
        }
        try:
            r = await self._client.post("/api/episodes/blacklist", data=data)
        except Exception as e:
            raise IntegrationError(f"bazarr blacklist: {e}") from e
        if r.status_code >= 400:
            raise IntegrationError(f"bazarr blacklist HTTP {r.status_code}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError:
            return None

    async def blacklist_movie(
        self, *, radarr_id: int, provider: str, subs_id: str,
        language: str, subtitles_path: str,
    ) -> dict[str, Any] | None:
        """Movie counterpart."""
        data = {
            "radarrid": str(radarr_id),
            "provider": provider,
            "subs_id": subs_id,
            "language": language,
            "subtitles_path": subtitles_path,
        }
        try:
            r = await self._client.post("/api/movies/blacklist", data=data)
        except Exception as e:
            raise IntegrationError(f"bazarr blacklist: {e}") from e
        if r.status_code >= 400:
            raise IntegrationError(f"bazarr blacklist HTTP {r.status_code}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError:
            return None

    async def providers_status(self) -> list[dict[str, Any]]:
        """v1.1-J: GET /api/providers — list of enabled providers with
        current status (Good / History / specific error). 'History' or
        non-'Good' status indicates throttle/down. UI surfaces this as
        live availability."""
        d = await self._get("/api/providers")
        return d.get("data", []) if isinstance(d, dict) else []

    async def episodes_history(self, *, length: int = 1000) -> list[dict[str, Any]]:
        """v1.1-J: Pull subtitle download history. Each row carries
        provider, score, action, language, blacklisted, dont_matches
        — the raw material for the success leaderboard."""
        d = await self._get("/api/episodes/history", params={"length": length})
        return d.get("data", []) if isinstance(d, dict) else []

    async def movies_history(self, *, length: int = 1000) -> list[dict[str, Any]]:
        d = await self._get("/api/movies/history", params={"length": length})
        return d.get("data", []) if isinstance(d, dict) else []

    async def candidate_episode_subtitles(
        self, episode_id: int, language: str = "en",
    ) -> list[dict[str, Any]]:
        """v1.1-F: GET /api/providers/episodes?episodeid=&language= — asks
        Bazarr's enabled providers for candidate releases right now.

        Returns a list of candidates with `provider`, `score`, `release_info`,
        `forced`, `hi`, `uploader`. Sorted by score desc by Bazarr.

        We use this to short-circuit Whisper: if a human-translated sub
        already exists with a strong score, prefer it. Default `language='en'`
        matches our coverage rows."""
        d = await self._get(
            "/api/providers/episodes",
            params={"episodeid": episode_id, "language": language},
        )
        if isinstance(d, dict):
            return d.get("data", []) or []
        return d or []

    async def candidate_movie_subtitles(
        self, radarr_id: int, language: str = "en",
    ) -> list[dict[str, Any]]:
        """Movie counterpart of candidate_episode_subtitles."""
        d = await self._get(
            "/api/providers/movies",
            params={"radarrid": radarr_id, "language": language},
        )
        if isinstance(d, dict):
            return d.get("data", []) or []
        return d or []

    async def download_episode_candidate(
        self, *, episode_id: int, language: str, provider: str,
        subtitles_id: str, score: int, forced: bool = False, hi: bool = False,
    ) -> dict[str, Any] | None:
        """v1.1-F: POST /api/providers/episodes — tells Bazarr to fetch
        a specific candidate from the candidate list above. Closes the
        arbiter loop: user picks a human sub instead of Whisper, Bazarr
        downloads + writes to disk + indexes."""
        data = {
            "episodeid": str(episode_id),
            "language": language,
            "provider": provider,
            "subtitles_id": subtitles_id,
            "score": str(score),
            "forced": "true" if forced else "false",
            "hi": "true" if hi else "false",
        }
        try:
            r = await self._client.post("/api/providers/episodes", data=data)
        except Exception as e:
            raise IntegrationError(f"bazarr download_candidate: {e}") from e
        if r.status_code >= 400:
            raise IntegrationError(f"bazarr download_candidate HTTP {r.status_code}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError:
            return None

    async def upload_episode_subtitle(
        self, *, series_id: int, episode_id: int, language: str,
        file_path: str, hi: bool = False, forced: bool = False,
    ) -> dict[str, Any] | None:
        """v1.1-G: POST /api/episodes/subtitles — multipart upload of a
        single .srt file. Closes the write-back loop with Bazarr directly
        instead of relying on the scan-disk task to discover it later.

        Bazarr expects form fields (seriesid, episodeid, language, forced,
        hi) + a `file` part. Returns Bazarr's response body on 2xx, raises
        IntegrationError on 4xx/5xx so callers can fall back to scan-disk.

        `file_path` must be readable from subarr's container — typically
        the freshly-Whispered .srt under /media/library/."""
        if not self._configured:
            raise IntegrationError("bazarr: not configured")
        # httpx multipart: open the file synchronously in a thread? Files
        # are tiny (~10-200KB for a typical sub), inline read is fine.
        with open(file_path, "rb") as fh:
            files = {"file": (file_path.rsplit("/", 1)[-1], fh, "application/x-subrip")}
            data = {
                "seriesid": str(series_id),
                "episodeid": str(episode_id),
                "language": language,
                "forced": "true" if forced else "false",
                "hi": "true" if hi else "false",
            }
            try:
                r = await self._client.post(
                    "/api/episodes/subtitles", data=data, files=files,
                )
            except Exception as e:
                raise IntegrationError(f"bazarr upload: {e}") from e
        if r.status_code >= 400:
            raise IntegrationError(
                f"bazarr upload HTTP {r.status_code}: {r.text[:300]}"
            )
        try:
            return r.json()
        except ValueError:
            return None

    async def upload_movie_subtitle(
        self, *, radarr_id: int, language: str, file_path: str,
        hi: bool = False, forced: bool = False,
    ) -> dict[str, Any] | None:
        """Movie counterpart of upload_episode_subtitle."""
        if not self._configured:
            raise IntegrationError("bazarr: not configured")
        with open(file_path, "rb") as fh:
            files = {"file": (file_path.rsplit("/", 1)[-1], fh, "application/x-subrip")}
            data = {
                "radarrid": str(radarr_id),
                "language": language,
                "forced": "true" if forced else "false",
                "hi": "true" if hi else "false",
            }
            try:
                r = await self._client.post(
                    "/api/movies/subtitles", data=data, files=files,
                )
            except Exception as e:
                raise IntegrationError(f"bazarr upload: {e}") from e
        if r.status_code >= 400:
            raise IntegrationError(
                f"bazarr upload HTTP {r.status_code}: {r.text[:300]}"
            )
        try:
            return r.json()
        except ValueError:
            return None

    async def trigger_task(self, task_id: str) -> None:
        """POST /api/system/tasks?taskid=<id> — kicks a scheduled task to
        run now. We use this to fire 'sync_episodes' / 'update_series' /
        per-series scan-disk after subgen writes a new .srt.

        Task IDs Bazarr exposes (verified against bazarr 1.5.x): see
        /api/system/tasks GET for the live list."""
        await self._post("/api/system/tasks", params={"taskid": task_id})

    async def list_tasks(self) -> list[dict[str, Any]]:
        d = await self._get("/api/system/tasks")
        return d.get("data", []) if isinstance(d, dict) else []
