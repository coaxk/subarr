"""#253: contract tests for the core integrations.

subarr is an API CLIENT of Bazarr/Sonarr/Radarr (no fork — so #171's rebase
detector doesn't apply here). The risk is API-contract drift: an upstream
version changes a response shape and our parser silently chokes. These pin the
shapes we actually depend on, using committed fixtures under
tests/fixtures/integrations/ as a living record of "what upstream returns".

Scope = the load-bearing endpoints (the coverage engine's inputs). They catch
OUR parser regressions and document the contract; they do NOT detect upstream
changing its API (that needs changelog-watching / a live canary — see #253).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from subarr.circuit_breaker import CircuitBreaker
from subarr.integrations.bazarr import BazarrClient
from subarr.integrations.radarr import RadarrClient
from subarr.integrations.sonarr import SonarrClient

_FIX = Path(__file__).parent / "fixtures" / "integrations"


def _load(name: str) -> object:
    return json.loads((_FIX / name).read_text(encoding="utf-8"))


def _client(cls, payload):
    """A real client whose transport serves the fixture (no env/network).
    Built via __new__ so it doesn't depend on settings — we set the few attrs
    the request path needs (mirrors the conftest stub)."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    c = cls.__new__(cls)
    c._base_url = "http://up.test"
    c._configured = True
    c._breaker = CircuitBreaker()
    c._client = httpx.AsyncClient(base_url="http://up.test", transport=httpx.MockTransport(handler))
    return c


# ── Bazarr ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bazarr_episodes_wanted_contract():
    c = _client(BazarrClient, _load("bazarr_episodes_wanted.json"))
    items = await c.episodes_wanted()  # unwraps the {"data": [...]} envelope
    assert isinstance(items, list) and items, "expected episodes under data[]"
    ep = items[0]
    for field in ("sonarrSeriesId", "sonarrEpisodeId", "seriesTitle", "episodeTitle", "episode_number"):
        assert field in ep, f"Bazarr wanted-episode contract lost field: {field}"
    assert isinstance(ep["missing_subtitles"], list)


@pytest.mark.asyncio
async def test_bazarr_movies_wanted_contract():
    c = _client(BazarrClient, _load("bazarr_movies_wanted.json"))
    items = await c.movies_wanted()
    assert isinstance(items, list) and items
    mv = items[0]
    for field in ("radarrId", "title", "missing_subtitles"):
        assert field in mv, f"Bazarr wanted-movie contract lost field: {field}"


@pytest.mark.asyncio
async def test_bazarr_badges_contract():
    c = _client(BazarrClient, _load("bazarr_badges.json"))
    badges = await c.badges()
    for field in ("episodes", "movies", "providers"):
        assert field in badges, f"Bazarr badges contract lost field: {field}"


@pytest.mark.asyncio
async def test_bazarr_provider_candidates_contract():
    c = _client(BazarrClient, _load("bazarr_providers_episodes.json"))
    cands = await c.candidate_episode_subtitles(episode_id=345)
    assert isinstance(cands, list) and cands
    cand = cands[0]
    # the fields the Whisper short-circuit decision reads
    for field in ("provider", "score", "forced", "hi"):
        assert field in cand, f"Bazarr provider-candidate contract lost field: {field}"


# ── Sonarr / Radarr ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sonarr_status_contract():
    c = _client(SonarrClient, _load("sonarr_system_status.json"))
    status = await c.status()
    assert status.get("version"), "Sonarr status must carry a version"
    assert "appName" in status


@pytest.mark.asyncio
async def test_sonarr_series_contract():
    c = _client(SonarrClient, _load("sonarr_series.json"))
    series = await c.series()
    assert isinstance(series, list) and series
    s = series[0]
    for field in ("id", "title", "path", "monitored"):
        assert field in s, f"Sonarr series contract lost field: {field}"


@pytest.mark.asyncio
async def test_radarr_status_contract():
    c = _client(RadarrClient, _load("radarr_system_status.json"))
    status = await c.status()
    assert status.get("version"), "Radarr status must carry a version"


@pytest.mark.asyncio
async def test_radarr_movies_contract():
    c = _client(RadarrClient, _load("radarr_movies.json"))
    movies = await c.movies()
    assert isinstance(movies, list) and movies
    m = movies[0]
    for field in ("id", "title", "path", "monitored"):
        assert field in m, f"Radarr movie contract lost field: {field}"
