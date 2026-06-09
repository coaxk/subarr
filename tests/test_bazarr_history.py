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
    c._client = httpx.AsyncClient(
        base_url="http://bazarr:6767", transport=httpx.MockTransport(handler)
    )
    c._configured = True
    return c


def test_episodes_history_passes_sonarr_episode_id():
    """Per-episode lookup (the provenance path) sends sonarrEpisodeId and does
    NOT raise TypeError — the bug was the shadowing keyword-only redefinition."""
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["path"] = req.url.path
        captured["query"] = dict(req.url.params)
        return httpx.Response(200, json={"data": [{"provider": "opensubtitles"}]})

    rows = asyncio.run(_client(handler).episodes_history(sonarr_episode_id=42))
    assert captured["path"] == "/api/episodes/history"
    assert captured["query"].get("sonarrEpisodeId") == "42"
    assert rows == [{"provider": "opensubtitles"}]


def test_episodes_history_full_pull_still_works():
    """The leaderboard path passes only length — must still work post-merge."""
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["query"] = dict(req.url.params)
        return httpx.Response(200, json={"data": []})

    asyncio.run(_client(handler).episodes_history(length=2000))
    assert captured["query"].get("length") == "2000"
    assert "sonarrEpisodeId" not in captured["query"]


def test_movies_history_passes_radarr_id():
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["query"] = dict(req.url.params)
        return httpx.Response(200, json={"data": []})

    asyncio.run(_client(handler).movies_history(radarr_movie_id=7))
    assert captured["query"].get("radarrId") == "7"


def test_error_path_raises_integration_error_not_nameerror():
    """An HTTP error in a bazarr.py method must raise IntegrationError — before
    the missing-import fix this raised NameError: name 'IntegrationError'."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(IntegrationError):
        asyncio.run(
            _client(handler).blacklist_episode(
                series_id=1, episode_id=2, provider="x",
                subs_id="s", language="en", subtitles_path="/p.srt",
            )
        )
