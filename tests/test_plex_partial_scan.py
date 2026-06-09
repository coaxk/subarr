"""Tests for the Plex partial-scan client (v1.1.1).

Covers the three things that determine whether the Apple-TV loop closes:
1. Path translation when subarr + Plex see different mount paths.
2. Section auto-discovery (longest-prefix match on Plex Location.path).
3. Partial-scan request shape (correct URL + path query parameter).
"""

from __future__ import annotations

import pytest
import httpx

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


def _client(handler) -> PlexClient:
    """Build a PlexClient with an httpx mock transport pinned to handler.

    PlexClient creates its own AsyncClient per request, so we monkey-patch
    httpx.AsyncClient via a factory below."""
    return PlexClient(
        base_url="http://plex.test:32400",
        token="testtoken",
        default_section="all",
        path_prefix="/data/Media",
        media_root="/media/library",
    )


@pytest.fixture
def patched_httpx(monkeypatch):
    """Patches httpx.AsyncClient so PlexClient sees a MockTransport routed
    to whatever request handler the test sets via the returned setter."""
    state = {"handler": None}
    # Capture the REAL AsyncClient before we patch the name, so the stub
    # can instantiate one without triggering infinite recursion.
    _RealAsyncClient = httpx.AsyncClient

    class _StubAsyncClient:
        def __init__(self, *a, **kw):
            self._real = _RealAsyncClient(
                transport=httpx.MockTransport(state["handler"]),
                timeout=kw.get("timeout", 5.0),
            )

        async def __aenter__(self):
            return self._real

        async def __aexit__(self, *a):
            await self._real.aclose()

        async def get(self, *a, **kw):
            return await self._real.get(*a, **kw)

        async def aclose(self):
            await self._real.aclose()

    monkeypatch.setattr("httpx.AsyncClient", _StubAsyncClient)
    return state


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


@pytest.mark.asyncio
async def test_partial_scan_discovers_section_and_fires(patched_httpx):
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

    patched_httpx["handler"] = handler
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
async def test_partial_scan_uses_numeric_section_without_discovery(patched_httpx):
    """If PLEX_SECTION is numeric, skip section listing — just fire."""
    captured = []

    def handler(req):
        captured.append(req)
        if req.url.path == "/library/sections":
            # Should NOT be called when section is pinned.
            return httpx.Response(500, text="should not be called")
        return httpx.Response(200, text="")

    patched_httpx["handler"] = handler
    c = PlexClient(
        base_url="http://plex.test:32400",
        token="t",
        default_section="7",
        path_prefix="/data/Media",
        media_root="/media/library",
    )
    result = await c.partial_scan("/media/library/TV/X.srt")
    assert result["section"] == "7"
    assert all(r.url.path != "/library/sections" for r in captured)


@pytest.mark.asyncio
async def test_partial_scan_raises_when_no_section_matches(patched_httpx):
    """Path outside every Plex Location → IntegrationError, not silent no-op.
    Caller (completion_watcher) catches + logs; we don't want to drop the
    failure on the floor inside the client."""

    def handler(req):
        return httpx.Response(200, text=SECTIONS_XML)

    patched_httpx["handler"] = handler
    c = PlexClient(
        base_url="http://plex.test:32400",
        token="t",
        default_section="all",
        path_prefix="/data/Media",
        media_root="/media/library",
    )
    with pytest.raises(IntegrationError, match="no section matched"):
        await c.partial_scan("/media/library/Music/x.srt")


def test_is_configured_requires_url_and_token():
    assert PlexClient("", "t", "all").is_configured() is False
    assert PlexClient("http://p:32400", "", "all").is_configured() is False
    assert PlexClient("http://p:32400", "t", "all").is_configured() is True
