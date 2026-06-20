"""Tests for the Plex partial-scan client (v1.1.1 + #290 pool/breaker).

Covers:
1. Path translation when subarr + Plex see different mount paths.
2. Section auto-discovery (longest-prefix match on Plex Location.path).
3. Partial-scan request shape (correct URL + path query parameter).
4. (#290) Persistent client — same object across calls (no per-call client).
5. (#290) Circuit breaker opens after consecutive 5xx; next call is
   short-circuited (transport NOT hit again).
6. (#290) 4xx does NOT open the breaker (record_success path).
7. (#290) aclose() really closes the underlying client.
"""

from __future__ import annotations

import pytest
import httpx

from subarr.circuit_breaker import CircuitBreaker
from subarr.integrations import IntegrationError
from subarr.integrations.plex import PlexClient


SECTIONS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<MediaContainer size="2">
  <Directory key="3" title="TV Shows">
    <Location id="11" path="/data/Media/TV"/>
  </Directory>
  <Directory key="5" title="Movies">
    <Location id="22" path="/data/Media/Movies"/>
  </Directory>
</MediaContainer>
"""


def _client(handler, breaker=None) -> PlexClient:
    """Build a PlexClient whose internal httpx client uses a MockTransport.

    #290: PlexClient now holds a persistent self._client. We build the real
    PlexClient then swap its _client with a mocked one — the same pattern
    used by test_integration_breaker.py for IntegrationClient."""
    c = PlexClient(
        base_url="http://plex.test:32400",
        token="testtoken",
        default_section="all",
        path_prefix="/data/Media",
        media_root="/media/library",
        breaker=breaker,
    )
    c._client = httpx.AsyncClient(
        base_url="http://plex.test:32400",
        transport=httpx.MockTransport(handler),
    )
    return c


# ── path-translation tests (no HTTP, no async) ───────────────────────────────


def test_translate_path_when_prefix_differs():
    c = PlexClient(
        base_url="http://p:32400",
        token="t",
        default_section="all",
        path_prefix="/data/Media",
        media_root="/media/library",
    )
    assert c.translate_path("/media/library/TV/Foo/S01E01.srt") == "/data/Media/TV/Foo/S01E01.srt"


def test_translate_path_identity_when_prefixes_unset():
    c = PlexClient(
        base_url="http://p:32400",
        token="t",
        default_section="all",
        path_prefix="",
        media_root="/media/library",
    )
    # No prefix → no translation
    assert c.translate_path("/media/library/TV/Foo/S01E01.srt") == "/media/library/TV/Foo/S01E01.srt"


def test_translate_path_passthrough_when_no_root_match():
    c = PlexClient(
        base_url="http://p:32400",
        token="t",
        default_section="all",
        path_prefix="/data/Media",
        media_root="/media/library",
    )
    # Path that doesn't start with media_root passes through untranslated.
    assert c.translate_path("/somewhere/else/file.srt") == "/somewhere/else/file.srt"


def test_is_configured_requires_url_and_token():
    assert PlexClient("", "t", "all").is_configured() is False
    assert PlexClient("http://p:32400", "", "all").is_configured() is False
    assert PlexClient("http://p:32400", "t", "all").is_configured() is True


# ── scan / section-discovery tests ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_partial_scan_discovers_section_and_fires():
    """Happy path: PLEX_SECTION='all' so the client lists sections, matches
    the file path against Location entries, and fires a refresh against the
    matching numeric section id with ?path=<dir>."""
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        if req.url.path == "/library/sections":
            return httpx.Response(200, text=SECTIONS_XML)
        if req.url.path.startswith("/library/sections/") and req.url.path.endswith("/refresh"):
            return httpx.Response(200, text="")
        return httpx.Response(404)

    c = _client(handler)
    result = await c.partial_scan("/media/library/TV/Foo/S01E01.srt")

    assert result["triggered"] is True
    assert result["section"] == "3"  # TV Shows
    assert result["scope"] == "partial"
    assert result["plex_path"] == "/data/Media/TV/Foo"
    # Two requests fired: sections list + refresh
    paths = [r.url.path for r in captured]
    assert "/library/sections" in paths
    assert "/library/sections/3/refresh" in paths
    refresh_req = next(r for r in captured if r.url.path.endswith("/refresh"))
    assert refresh_req.url.params["path"] == "/data/Media/TV/Foo"
    assert refresh_req.url.params["X-Plex-Token"] == "testtoken"


@pytest.mark.asyncio
async def test_partial_scan_uses_numeric_section_without_discovery():
    """If PLEX_SECTION is numeric, skip section listing — just fire."""
    captured = []

    def handler(req):
        captured.append(req)
        if req.url.path == "/library/sections":
            # Should NOT be called when section is pinned.
            return httpx.Response(500, text="should not be called")
        return httpx.Response(200, text="")

    c = PlexClient(
        base_url="http://plex.test:32400",
        token="t",
        default_section="7",
        path_prefix="/data/Media",
        media_root="/media/library",
    )
    c._client = httpx.AsyncClient(
        base_url="http://plex.test:32400",
        transport=httpx.MockTransport(handler),
    )
    result = await c.partial_scan("/media/library/TV/X.srt")
    assert result["section"] == "7"
    assert all(r.url.path != "/library/sections" for r in captured)


@pytest.mark.asyncio
async def test_partial_scan_raises_when_no_section_matches():
    """Path outside every Plex Location → IntegrationError, not silent no-op."""

    def handler(req):
        return httpx.Response(200, text=SECTIONS_XML)

    c = PlexClient(
        base_url="http://plex.test:32400",
        token="t",
        default_section="all",
        path_prefix="/data/Media",
        media_root="/media/library",
    )
    c._client = httpx.AsyncClient(
        base_url="http://plex.test:32400",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(IntegrationError, match="no section matched"):
        await c.partial_scan("/media/library/Music/x.srt")


# ── #290: pooling + breaker tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_persistent_client_same_object_across_calls():
    """#290: _client must be the SAME object across multiple calls — no per-call
    client construction (which would defeat connection pooling)."""
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if req.url.path == "/library/sections":
            return httpx.Response(200, text=SECTIONS_XML)
        return httpx.Response(200, text="")

    c = _client(handler)
    client_before = c._client

    await c.partial_scan("/media/library/TV/Foo/S01E01.srt")
    # Flush the section cache so the second call re-hits the transport.
    c._sections = None
    await c.partial_scan("/media/library/TV/Foo/S01E02.srt")

    assert c._client is client_before, "_client was replaced between calls — pooling broken"
    assert calls["n"] >= 2  # at least two real requests went through


@pytest.mark.asyncio
async def test_breaker_opens_on_5xx_and_short_circuits():
    """#290: after fail_threshold consecutive 5xx the breaker opens and the
    next call raises 'circuit open' WITHOUT touching the transport."""
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, text="down")

    cb = CircuitBreaker(name="plex", fail_threshold=3, cooldown_s=60, clock=lambda: 0.0)
    c = _client(handler, breaker=cb)

    # Drive three 5xx to trip the breaker.
    for _ in range(3):
        with pytest.raises(IntegrationError):
            await c._request("/library/sections", {"X-Plex-Token": "t"})

    assert cb.state == "open"
    n_after_trip = calls["n"]

    # Next call must be short-circuited — transport NOT hit.
    with pytest.raises(IntegrationError, match="circuit open"):
        await c._request("/library/sections", {"X-Plex-Token": "t"})

    assert calls["n"] == n_after_trip, "transport was hit after breaker opened — short-circuit broken"


@pytest.mark.asyncio
async def test_transport_error_trips_breaker():
    """#290: a transport-level error (ConnectError) also counts as a failure."""
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("refused")

    cb = CircuitBreaker(name="plex", fail_threshold=2, cooldown_s=60, clock=lambda: 0.0)
    c = _client(handler, breaker=cb)

    for _ in range(2):
        with pytest.raises(IntegrationError):
            await c._request("/identity", {"X-Plex-Token": "t"})

    assert cb.state == "open"
    n_after = calls["n"]

    with pytest.raises(IntegrationError, match="circuit open"):
        await c._request("/identity", {"X-Plex-Token": "t"})

    assert calls["n"] == n_after


@pytest.mark.asyncio
async def test_4xx_does_not_open_breaker():
    """#290: 4xx means Plex is reachable (bad auth/request) — must NOT trip
    the breaker even after many consecutive 4xx responses."""
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, text="Unauthorized")

    cb = CircuitBreaker(name="plex", fail_threshold=3, cooldown_s=60, clock=lambda: 0.0)
    c = _client(handler, breaker=cb)

    # Fire five 401s — well over the threshold.
    for _ in range(5):
        with pytest.raises(IntegrationError):
            await c._request("/library/sections", {"X-Plex-Token": "bad"})

    assert cb.state == "closed", "401s must not open the circuit breaker"
    assert calls["n"] == 5  # every call reached the transport


@pytest.mark.asyncio
async def test_aclose_closes_underlying_client():
    """#290: aclose() must delegate to the httpx client so the connection pool
    is actually released (not a no-op as the old stateless implementation was)."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="")

    c = _client(handler)
    assert not c._client.is_closed, "client should be open before aclose()"
    await c.aclose()
    assert c._client.is_closed, "client must be closed after aclose()"
