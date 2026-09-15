"""Regression tests for two bugs surfaced by the ruff lint gate (2026-06-09):

1. `BazarrClient` defined `episodes_history` / `movies_history` TWICE. The
   later keyword-only `(*, length)` versions shadowed the general
   `(id=None, length)` ones, so `provenance.py`'s per-episode call
   `episodes_history(sonarr_episode_id=...)` hit the shadow and raised
   TypeError at runtime. (F811)
2. `bazarr.py` raised `IntegrationError` in 14 error paths without importing
   it → those paths raised NameError instead. (F821)

These assert the per-id history call works AND that an error path raises the
intended IntegrationError, both of which the happy-path suite never exercised.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from subarr.integrations import IntegrationError
from subarr.integrations.bazarr import BazarrClient


def _client(handler) -> BazarrClient:
    c = BazarrClient()
    c._client = httpx.AsyncClient(base_url="http://bazarr:6767", transport=httpx.MockTransport(handler))
    c._configured = True
    return c


# #550: Bazarr's history API filters on `episodeid` / `radarrid`
# (bazarr/api/{episodes,movies}/history.py, identical in 1.6.0 and 1.6.1) and
# silently IGNORES any other query parameter. subarr sent `sonarrEpisodeId` /
# `radarrId` from v1.1 on, so every per-file lookup returned recent history for
# the whole library. The old tests asserted the name subarr sent, not the name
# Bazarr reads, so they could not fail. This stub behaves like Bazarr instead.
_EP_ROWS = [
    {"sonarrEpisodeId": 42, "provider": "opensubtitles", "subs_id": "a"},
    {"sonarrEpisodeId": 99, "provider": "subdl", "subs_id": "b"},
    {"sonarrEpisodeId": 42, "provider": "gestdown", "subs_id": "c"},
]
_MOVIE_ROWS = [
    {"radarrId": 7, "provider": "opensubtitles", "subs_id": "m1"},
    {"radarrId": 8, "provider": "subdl", "subs_id": "m2"},
]


def _bazarr_like(req: httpx.Request) -> httpx.Response:
    q = req.url.params
    if req.url.path == "/api/episodes/history":
        rows = _EP_ROWS
        if "episodeid" in q:
            rows = [r for r in rows if r["sonarrEpisodeId"] == int(q["episodeid"])]
        return httpx.Response(200, json={"data": rows, "total": len(rows)})
    if req.url.path == "/api/movies/history":
        rows = _MOVIE_ROWS
        if "radarrid" in q:
            rows = [r for r in rows if r["radarrId"] == int(q["radarrid"])]
        return httpx.Response(200, json={"data": rows, "total": len(rows)})
    return httpx.Response(404)


def _ignores_filters(req: httpx.Request) -> httpx.Response:
    """A Bazarr that ignores the id filter entirely (older, newer or broken)."""
    if req.url.path == "/api/episodes/history":
        return httpx.Response(200, json={"data": _EP_ROWS})
    return httpx.Response(200, json={"data": _MOVIE_ROWS})


def test_episodes_history_returns_only_that_episode():
    """Per-episode lookup (the provenance and blacklist path) must reach Bazarr's
    real filter. Also guards the old F811 shadowing: this call used to TypeError."""
    rows = asyncio.run(_client(_bazarr_like).episodes_history(sonarr_episode_id=42))
    assert [r["subs_id"] for r in rows] == ["a", "c"]


def test_movies_history_returns_only_that_movie():
    rows = asyncio.run(_client(_bazarr_like).movies_history(radarr_movie_id=7))
    assert [r["subs_id"] for r in rows] == ["m1"]


def test_history_sends_bazarrs_parameter_names():
    captured = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(dict(req.url.params))
        return httpx.Response(200, json={"data": []})

    c = _client(handler)
    asyncio.run(c.episodes_history(sonarr_episode_id=42))
    asyncio.run(c.movies_history(radarr_movie_id=7))
    assert captured[0].get("episodeid") == "42"
    assert captured[1].get("radarrid") == "7"


def test_history_drops_rows_for_other_items_when_bazarr_ignores_the_filter():
    """Backstop: another item's subtitle must never come back for a per-file
    lookup, because the blacklist panel puts a Blacklist button on every row."""
    c = _client(_ignores_filters)
    eps = asyncio.run(c.episodes_history(sonarr_episode_id=42))
    movs = asyncio.run(c.movies_history(radarr_movie_id=7))
    assert [r["subs_id"] for r in eps] == ["a", "c"]
    assert [r["subs_id"] for r in movs] == ["m1"]


def test_episodes_history_full_pull_still_works():
    """The leaderboard path passes only length and wants every row."""
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["query"] = dict(req.url.params)
        return httpx.Response(200, json={"data": _EP_ROWS})

    rows = asyncio.run(_client(handler).episodes_history(length=2000))
    assert captured["query"].get("length") == "2000"
    assert "episodeid" not in captured["query"]
    assert len(rows) == 3


def test_movies_history_full_pull_still_works():
    rows = asyncio.run(_client(_ignores_filters).movies_history(length=2000))
    assert len(rows) == 2


def test_error_path_raises_integration_error_not_nameerror():
    """An HTTP error in a bazarr.py method must raise IntegrationError — before
    the missing-import fix this raised NameError: name 'IntegrationError'."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(IntegrationError):
        asyncio.run(
            _client(handler).blacklist_episode(
                series_id=1,
                episode_id=2,
                provider="x",
                subs_id="s",
                language="en",
                subtitles_path="/p.srt",
            )
        )
