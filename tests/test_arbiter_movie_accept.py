"""Arbiter movie-accept (POST /api/arbiter/accept with movie_id).

GET /candidates already supports movies; accept raised 501 for them. These
pin the movie path to mirror the episode passthrough to Bazarr
(/api/providers/movies, form field radarrid).
"""

from __future__ import annotations

import asyncio

import httpx


def test_download_movie_candidate_posts_radarrid():
    """Unit: the new Bazarr method must POST to /api/providers/movies with a
    `radarrid` form field (movies use radarrid, not episodeid)."""
    from subarr.integrations.bazarr import BazarrClient

    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["path"] = req.url.path
        captured["body"] = req.content.decode()
        return httpx.Response(200, json={"downloaded": True})

    c = BazarrClient()
    c._client = httpx.AsyncClient(base_url="http://bazarr:6767", transport=httpx.MockTransport(handler))
    c._configured = True

    res = asyncio.run(
        c.download_movie_candidate(
            movie_id=42,
            language="en",
            provider="opensubtitles",
            subtitles_id="abc123",
            score=91,
            forced=False,
            hi=False,
        )
    )
    assert captured["path"] == "/api/providers/movies"
    assert "radarrid=42" in captured["body"]
    assert "episodeid" not in captured["body"]
    assert res == {"downloaded": True}


def test_accept_movie_routes_to_bazarr(app_with_stub):
    """Router: POST /accept with movie_id returns 200 and invokes the movie
    download (no more 501)."""
    c = app_with_stub
    calls = {}

    class FakeBazarr:
        def is_configured(self):
            return True

        async def download_movie_candidate(self, **kw):
            calls.update(kw)
            return {"downloaded": True}

        async def aclose(self):  # lifespan shutdown gathers bazarr.aclose()
            pass

    c.app.state.integrations.bazarr = FakeBazarr()
    r = c.post(
        "/api/arbiter/accept",
        json={
            "movie_id": 42,
            "provider": "opensubtitles",
            "subtitles_id": "abc123",
            "score": 91,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] is True
    assert calls["movie_id"] == 42


def test_accept_requires_an_id(app_with_stub):
    r = app_with_stub.post(
        "/api/arbiter/accept",
        json={
            "provider": "opensubtitles",
            "subtitles_id": "x",
            "score": 1,
        },
    )
    assert r.status_code == 400
