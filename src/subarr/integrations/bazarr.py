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
from . import IntegrationError
from .base import IntegrationClient


def _rows_for(rows: list[dict[str, Any]], id_field: str, wanted: int | None) -> list[dict[str, Any]]:
    """#550: keep only rows belonging to `wanted`. None means no filter (the
    leaderboard's whole-history pull)."""
    if wanted is None:
        return rows
    return [r for r in rows if isinstance(r, dict) and r.get(id_field) == wanted]


class BazarrClient(IntegrationClient):
    name = "bazarr"

    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        url = settings.bazarr_url if base_url is None else base_url
        key = settings.bazarr_api_key if api_key is None else api_key
        super().__init__(
            base_url=url if key else "",
            headers={"X-API-KEY": key} if key else None,
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

    async def episodes_history(
        self, sonarr_episode_id: int | None = None, length: int = 50
    ) -> list[dict[str, Any]]:
        """Per-episode subtitle download history (provider, score, timestamp).
        If sonarr_episode_id supplied, only that episode's rows are returned.

        #550: Bazarr's filter parameter is `episodeid` (the ROW field is
        `sonarrEpisodeId`). Bazarr ignores unknown parameters, so the wrong name
        returns the whole library's recent history, and the blacklist panel then
        offers a Blacklist button on other items' subtitles. The rows are also
        filtered here, so a Bazarr that ignores the filter still cannot do that."""
        params: dict[str, Any] = {"length": length}
        if sonarr_episode_id is not None:
            params["episodeid"] = sonarr_episode_id
        d = await self._get("/api/episodes/history", params=params)
        rows = d.get("data", []) if isinstance(d, dict) else []
        return _rows_for(rows, "sonarrEpisodeId", sonarr_episode_id)

    async def movies_history(
        self, radarr_movie_id: int | None = None, length: int = 50
    ) -> list[dict[str, Any]]:
        """Movie counterpart. #550: the filter parameter is `radarrid`; the row
        field is `radarrId`."""
        params: dict[str, Any] = {"length": length}
        if radarr_movie_id is not None:
            params["radarrid"] = radarr_movie_id
        d = await self._get("/api/movies/history", params=params)
        rows = d.get("data", []) if isinstance(d, dict) else []
        return _rows_for(rows, "radarrId", radarr_movie_id)

    async def blacklist_episode(
        self,
        *,
        series_id: int,
        episode_id: int,
        provider: str,
        subs_id: str,
        language: str,
        subtitles_path: str,
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
        self,
        *,
        radarr_id: int,
        provider: str,
        subs_id: str,
        language: str,
        subtitles_path: str,
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

    async def candidate_episode_subtitles(
        self,
        episode_id: int,
        language: str = "en",
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
        self,
        radarr_id: int,
        language: str = "en",
    ) -> list[dict[str, Any]]:
        """Movie counterpart of candidate_episode_subtitles."""
        d = await self._get(
            "/api/providers/movies",
            params={"radarrid": radarr_id, "language": language},
        )
        if isinstance(d, dict):
            return d.get("data", []) or []
        return d or []

    async def episode_series_id(self, episode_id: int) -> int | None:
        """#591: resolve an episode id to its Sonarr series id, which Bazarr's
        download endpoint requires but the coverage row does not carry.

        Deliberately asked of the SAME Bazarr instance the download will be
        POSTed to, rather than of Sonarr. On a multi-instance install that
        makes the answer correct by construction - the instance that owns the
        episode is the instance that will fetch the subtitle - so no
        canonical-path hint is needed to scope it, and we add no new
        dependency to a path that otherwise needs only Bazarr.

        The `[]` in `episodeid[]` is part of the parameter NAME, not an array
        convention: `api/episodes/episodes.py` declares
        `add_argument('episodeid[]', type=int, action='append', ...)` verbatim,
        so a bare `episodeid` is not a parameter at all and the endpoint
        answers 404 'Series or Episode ID not provided'. Read from the source
        and confirmed live against v1.6.2 on 2026-10-01; re-check when bumping
        .github/bazarr-verified-version.
        """
        d = await self._get("/api/episodes", params={"episodeid[]": episode_id})
        rows = d.get("data", []) if isinstance(d, dict) else d
        if not isinstance(rows, list):
            return None
        for row in rows:
            if not isinstance(row, dict):
                continue
            sid = row.get("sonarrSeriesId")
            if sid is None:
                continue
            try:
                return int(sid)
            except (TypeError, ValueError):
                return None
        return None

    async def download_episode_candidate(
        self,
        *,
        episode_id: int,
        series_id: int,
        provider: str,
        subtitles_id: str,
        forced: bool = False,
        hi: bool = False,
        original_format: bool = False,
    ) -> dict[str, Any] | None:
        """v1.1-F: POST /api/providers/episodes — tells Bazarr to fetch
        a specific candidate from the candidate list above. Closes the
        arbiter loop: user picks a human sub instead of Whisper, Bazarr
        downloads + writes to disk + indexes.

        #591: this payload was wrong from the start and could never succeed.
        Bazarr's post_request_parser declares seriesid, episodeid, hi, forced,
        original_format, provider and subtitle ALL required=True, and the
        handler calls parse_args() - so the old payload, which omitted
        `seriesid` and `original_format` and sent the id as `subtitles_id`,
        was a 400 every time. `language` and `score` are not parameters on
        this endpoint at all (the language is implied by the subtitle id), and
        Bazarr ignores unknown arguments silently, which is why nothing ever
        complained. Contract read from a live v1.6.2 container 2026-10-01;
        re-check it when bumping .github/bazarr-verified-version.

        The booleans go as "true"/"false" deliberately: Bazarr calls
        args.get('hi').capitalize(), and "true".capitalize() is "True" - so
        lowercase is correct here and "TRUE" would not be."""
        data = {
            "seriesid": str(series_id),
            "episodeid": str(episode_id),
            "provider": provider,
            "subtitle": subtitles_id,
            "forced": "true" if forced else "false",
            "hi": "true" if hi else "false",
            "original_format": "true" if original_format else "false",
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

    async def download_movie_candidate(
        self,
        *,
        movie_id: int,
        provider: str,
        subtitles_id: str,
        forced: bool = False,
        hi: bool = False,
        original_format: bool = False,
    ) -> dict[str, Any] | None:
        """Movie mirror of download_episode_candidate: POST
        /api/providers/movies (form field `radarrid`, matching
        candidate_movie_subtitles + upload_movie_subtitle) so the arbiter
        accept loop works for movies too.

        #591: same fix as the episode twin. The movies parser wants radarrid,
        hi, forced, original_format, provider and subtitle - and notably NO
        seriesid, which is the one asymmetry between the two endpoints."""
        data = {
            "radarrid": str(movie_id),
            "provider": provider,
            "subtitle": subtitles_id,
            "forced": "true" if forced else "false",
            "hi": "true" if hi else "false",
            "original_format": "true" if original_format else "false",
        }
        try:
            r = await self._client.post("/api/providers/movies", data=data)
        except Exception as e:
            raise IntegrationError(f"bazarr download_movie_candidate: {e}") from e
        if r.status_code >= 400:
            raise IntegrationError(f"bazarr download_movie_candidate HTTP {r.status_code}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError:
            return None

    async def upload_episode_subtitle(
        self,
        *,
        series_id: int,
        episode_id: int,
        language: str,
        file_path: str,
        hi: bool = False,
        forced: bool = False,
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
                    "/api/episodes/subtitles",
                    data=data,
                    files=files,
                )
            except Exception as e:
                raise IntegrationError(f"bazarr upload: {e}") from e
        if r.status_code >= 400:
            raise IntegrationError(f"bazarr upload HTTP {r.status_code}: {r.text[:300]}")
        try:
            return r.json()
        except ValueError:
            return None

    async def upload_movie_subtitle(
        self,
        *,
        radarr_id: int,
        language: str,
        file_path: str,
        hi: bool = False,
        forced: bool = False,
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
                    "/api/movies/subtitles",
                    data=data,
                    files=files,
                )
            except Exception as e:
                raise IntegrationError(f"bazarr upload: {e}") from e
        if r.status_code >= 400:
            raise IntegrationError(f"bazarr upload HTTP {r.status_code}: {r.text[:300]}")
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
