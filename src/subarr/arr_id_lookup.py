"""#629: resolve a canonical path to its Sonarr/Radarr ids by asking the arrs.

A job queued by hand (Requeue, Regenerate, a Scan-tab submit) knows only the
file. The coverage snapshot fills ids for a still-WANTED file at enqueue, but it
is built from Bazarr's wanted list, so a file that already has a subtitle is
never in it — and that is the usual Requeue/Regenerate target. Without ids the
completion watcher can neither upload the subtitle to Bazarr nor trigger its
scan-disk, so Bazarr never hears about it.

The lookup runs at completion, off the request path, and only for a job that
lacks ids. Episode: the series whose folder holds the file, then the episode
file whose path matches, then the episode(s) on it. Movie: the movie whose file
(or, failing that, folder) matches. The series and movie lists are cached per
client for a few minutes so a batch of manual jobs costs one list call.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .paths import strip_arr_prefix

log = logging.getLogger(__name__)

LIST_CACHE_TTL_S = 300.0


def _canon(arr_path: Any) -> str:
    return (strip_arr_prefix(arr_path) or "").strip("/") if isinstance(arr_path, str) else ""


def _holds(folder: str, canonical: str) -> bool:
    return bool(folder) and canonical.startswith(folder + "/")


class ArrIdResolver:
    def __init__(self, ttl_s: float = LIST_CACHE_TTL_S):
        self._ttl_s = ttl_s
        self._lists: dict[tuple[int, str], tuple[float, list[dict[str, Any]]]] = {}

    async def _cached(self, client: Any, kind: str) -> list[dict[str, Any]]:
        key = (id(client), kind)
        hit = self._lists.get(key)
        now = time.monotonic()
        if hit is not None and now - hit[0] < self._ttl_s:
            return hit[1]
        rows = await getattr(client, kind)()
        rows = rows if isinstance(rows, list) else []
        self._lists[key] = (now, rows)
        return rows

    async def resolve(self, canonical: str, sonarr: Any, radarr: Any) -> dict[str, int]:
        """{series_id, sonarr_episode_id} or {radarr_movie_id}, or {} when no
        configured arr knows the file. Never raises: a failing arr is logged
        and treated as not knowing it."""
        canonical = (canonical or "").strip("/")
        if not canonical:
            return {}
        for name, client, fn in (("sonarr", sonarr, self._episode), ("radarr", radarr, self._movie)):
            if client is None or not client.is_configured():
                continue
            try:
                ids = await fn(canonical, client)
            except Exception as e:  # noqa: BLE001 - a lookup miss must never break a completion
                log.warning("arr id lookup: %s failed for %s: %s", name, canonical, e)
                continue
            if ids:
                return ids
        return {}

    async def _episode(self, canonical: str, sonarr: Any) -> dict[str, int]:
        # Longest folder wins, so 'Show' never claims a file under 'Show Extra'.
        series = sorted(
            (s for s in await self._cached(sonarr, "series") if _holds(_canon(s.get("path")), canonical)),
            key=lambda s: len(_canon(s.get("path"))),
            reverse=True,
        )
        if not series:
            return {}
        sid = int(series[0]["id"])
        efile = next(
            (f for f in await sonarr.episode_files_for_series(sid) if _canon(f.get("path")) == canonical),
            None,
        )
        if efile is None:
            return {}
        eps = [e for e in await sonarr.episodes(sid) if e.get("episodeFileId") == efile.get("id")]
        if not eps:
            return {}
        # A multi-episode file uploads against its first episode.
        first = min(eps, key=lambda e: (e.get("seasonNumber") or 0, e.get("episodeNumber") or 0))
        return {"series_id": sid, "sonarr_episode_id": int(first["id"])}

    async def _movie(self, canonical: str, radarr: Any) -> dict[str, int]:
        movies = await self._cached(radarr, "movies")
        exact = next((m for m in movies if _canon((m.get("movieFile") or {}).get("path")) == canonical), None)
        if exact is None:
            folders = sorted(
                (m for m in movies if _holds(_canon(m.get("path")), canonical)),
                key=lambda m: len(_canon(m.get("path"))),
                reverse=True,
            )
            exact = folders[0] if folders else None
        return {"radarr_movie_id": int(exact["id"])} if exact is not None else {}
