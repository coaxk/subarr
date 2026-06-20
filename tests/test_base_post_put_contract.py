"""#290: Fix 2 — base IntegrationClient _post/_put non-JSON body contract.

Before this fix:
  - empty body (204/200 with no content) → None  (correct)
  - non-empty non-JSON body (e.g. HTML error page on a 200) → None  (WRONG — silent data loss)
  - JSON body → dict  (correct)

After this fix:
  - empty body → None
  - non-empty non-JSON body → raises IntegrationError
  - JSON body → dict
"""

from __future__ import annotations

import pytest
import httpx

from subarr.integrations import IntegrationError
from subarr.integrations.base import IntegrationClient


def _client(handler) -> IntegrationClient:
    """Build an IntegrationClient whose transport is a MockTransport."""
    c = IntegrationClient("http://up.test")
    c._client = httpx.AsyncClient(
        base_url="http://up.test",
        transport=httpx.MockTransport(handler),
    )
    return c


# ── _post contract ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_empty_body_returns_none():
    """A 200 with an empty body (or whitespace only) must return None.
    Arr endpoints commonly return empty 200/204 for successful mutations."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    c = _client(handler)
    result = await c._post("/api/v3/something")
    assert result is None


@pytest.mark.asyncio
async def test_post_whitespace_body_returns_none():
    """Whitespace-only body (e.g. bare newline) counts as empty — return None."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\n  \t\n")

    c = _client(handler)
    result = await c._post("/api/v3/something")
    assert result is None


@pytest.mark.asyncio
async def test_post_json_body_returns_dict():
    """A 200 with a valid JSON body returns the decoded dict."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": 42, "status": "ok"})

    c = _client(handler)
    result = await c._post("/api/v3/something")
    assert result == {"id": 42, "status": "ok"}


@pytest.mark.asyncio
async def test_post_non_empty_non_json_raises_integration_error():
    """A non-empty non-JSON body on a 200 must raise IntegrationError.
    This catches HTML error pages that sneak through as 200 OK."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>Proxy error</html>")

    c = _client(handler)
    with pytest.raises(IntegrationError, match="non-json response"):
        await c._post("/api/v3/something")


# ── _put contract (mirrors _post) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_empty_body_returns_none():
    """PUT with an empty 200 body returns None (same contract as _post)."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    c = _client(handler)
    result = await c._put("/api/v3/episodeFile/99")
    assert result is None


@pytest.mark.asyncio
async def test_put_json_body_returns_dict():
    """PUT with a JSON body returns the decoded dict."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": 99, "languages": [{"name": "Danish"}]})

    c = _client(handler)
    result = await c._put("/api/v3/episodeFile/99")
    assert result == {"id": 99, "languages": [{"name": "Danish"}]}


@pytest.mark.asyncio
async def test_put_non_empty_non_json_raises_integration_error():
    """PUT with a non-empty non-JSON 200 body raises IntegrationError."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"plain text error")

    c = _client(handler)
    with pytest.raises(IntegrationError, match="non-json response"):
        await c._put("/api/v3/episodeFile/99")


# ── regression: existing callers that rely on None-for-empty-body ────────────


@pytest.mark.asyncio
async def test_post_204_no_content_returns_none():
    """HTTP 204 No Content has an empty body by definition — must be None."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(204, content=b"")

    c = _client(handler)
    result = await c._post("/api/v3/command")
    assert result is None
