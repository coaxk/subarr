"""#516: `audio_lang.py` documented Sonarr's /language table as "cached per
process" and `SonarrClient.languages()` was a bare GET with no memoization at
all. The table is static for the life of a Sonarr install (it changes on a
Sonarr upgrade, not per request), yet every propagated file re-fetched it, and
#496 removed the ceiling on how many files one bulk action propagates.

The cache is per CLIENT INSTANCE: the bundle holds one client per configured
Sonarr for the process lifetime and rebuilds them on a credential change, so
"per instance" is exactly "per process, until the instance is reconfigured".
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from subarr.integrations import IntegrationError
from subarr.integrations.sonarr import SonarrClient

ENGLISH = [{"id": 1, "name": "English"}]
ENGLISH_SPANISH = [{"id": 1, "name": "English"}, {"id": 3, "name": "Spanish"}]


def _client(handler) -> SonarrClient:
    c = SonarrClient(base_url="http://sonarr.test", api_key="k")
    c._client = httpx.AsyncClient(base_url="http://sonarr.test", transport=httpx.MockTransport(handler))
    return c


def _table_server(replies):
    """A /language endpoint that serves `replies` in order (a list of either a
    language list or an int status for a failure) and counts every hit."""
    hits = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v3/language", req.url.path
        i = min(hits["n"], len(replies) - 1)
        hits["n"] += 1
        r = replies[i]
        if isinstance(r, int):
            return httpx.Response(r, text="boom")
        return httpx.Response(200, json=r)

    return handler, hits


def test_second_call_is_served_from_the_instance_cache():
    handler, hits = _table_server([ENGLISH])
    c = _client(handler)

    async def go():
        a = await c.languages()
        b = await c.languages()
        return a, b

    a, b = asyncio.run(go())
    assert a == ENGLISH and b == ENGLISH
    assert hits["n"] == 1, "the table was fetched again although nothing invalidated it"


def test_refresh_bypasses_the_cache_and_replaces_it():
    handler, hits = _table_server([ENGLISH, ENGLISH_SPANISH])
    c = _client(handler)

    async def go():
        first = await c.languages()
        again = await c.languages(refresh=True)
        cached = await c.languages()
        return first, again, cached

    first, again, cached = asyncio.run(go())
    assert first == ENGLISH
    assert again == ENGLISH_SPANISH
    assert cached == ENGLISH_SPANISH, "the refreshed table must become the cached one"
    assert hits["n"] == 2


def test_a_failed_fetch_is_never_cached():
    handler, hits = _table_server([500, ENGLISH])
    c = _client(handler)

    async def go():
        with pytest.raises(IntegrationError):
            await c.languages()
        return await c.languages()

    assert asyncio.run(go()) == ENGLISH
    assert hits["n"] == 2, "a failure must not poison the cache into serving nothing forever"


def test_cache_is_per_instance_not_per_class():
    h1, hits1 = _table_server([ENGLISH])
    h2, hits2 = _table_server([ENGLISH_SPANISH])
    a = _client(h1)
    b = _client(h2)

    async def go():
        return await a.languages(), await b.languages(), await a.languages(), await b.languages()

    la, lb, la2, lb2 = asyncio.run(go())
    assert la == la2 == ENGLISH
    assert lb == lb2 == ENGLISH_SPANISH
    assert hits1["n"] == 1 and hits2["n"] == 1


def test_cache_expires_after_the_ttl(monkeypatch):
    from subarr.integrations import sonarr as mod

    handler, hits = _table_server([ENGLISH, ENGLISH_SPANISH])
    c = _client(handler)
    now = {"t": 1000.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: now["t"])

    async def go():
        first = await c.languages()
        now["t"] += mod.LANGUAGES_TTL_S - 1
        still = await c.languages()
        now["t"] += 2
        fresh = await c.languages()
        return first, still, fresh

    first, still, fresh = asyncio.run(go())
    assert first == still == ENGLISH
    assert fresh == ENGLISH_SPANISH
    assert hits["n"] == 2
