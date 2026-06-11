"""#198 — API key (opt-in) + CSRF origin gate (default on) for /api/*.

Threat model: subarr mutates Sonarr, triggers Bazarr tasks, edits library
roots, and can restart subgen via the docker socket — and historically
shipped with no API auth and nothing stopping a malicious webpage from
blind-POSTing at a known LAN IP through a victim's browser.

CSRF gate (everyone): unsafe /api methods require same-origin fetch
metadata when a browser sent any; header-less clients (curl, httpx, the
subgen webhook) pass — they are not CSRF vectors.

API key (opt-in via SUBARR_API_KEY): all /api/* requires X-Api-Key or
?apikey= (arr convention; the query form exists for subgen's webhook URL).
/api/health stays open for container healthchecks; /api/ui-bootstrap hands
the key to same-origin pages only, so the bundled UI keeps working.
"""

from __future__ import annotations

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route


def _ok(request):
    return PlainTextResponse("ok")


def _mini_app(middleware_cls, **kwargs):
    app = Starlette(
        routes=[
            Route("/api/thing", _ok, methods=["GET", "POST", "DELETE"]),
            Route("/api/health", _ok, methods=["GET"]),
            Route("/static/x.js", _ok, methods=["GET"]),
        ]
    )
    wrapped = middleware_cls(app, **kwargs)
    transport = httpx.ASGITransport(app=wrapped)
    return httpx.AsyncClient(transport=transport, base_url="http://subarr.test:9922")


async def _req(client, method, url, **kw):
    async with client as c:
        return await c.request(method, url, **kw)


# ─── CSRF origin gate ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_csrf_blocks_cross_site_fetch_metadata():
    from subarr.api_security import CsrfOriginMiddleware

    c = _mini_app(CsrfOriginMiddleware, enabled=True)
    r = await _req(c, "POST", "/api/thing", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


@pytest.mark.anyio
async def test_csrf_allows_same_origin_fetch_metadata():
    from subarr.api_security import CsrfOriginMiddleware

    c = _mini_app(CsrfOriginMiddleware, enabled=True)
    r = await _req(c, "POST", "/api/thing", headers={"Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200


@pytest.mark.anyio
async def test_csrf_blocks_mismatched_origin_without_fetch_metadata():
    from subarr.api_security import CsrfOriginMiddleware

    c = _mini_app(CsrfOriginMiddleware, enabled=True)
    r = await _req(c, "POST", "/api/thing", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


@pytest.mark.anyio
async def test_csrf_allows_matching_origin():
    from subarr.api_security import CsrfOriginMiddleware

    c = _mini_app(CsrfOriginMiddleware, enabled=True)
    r = await _req(
        c, "POST", "/api/thing", headers={"Origin": "http://subarr.test:9922", "Host": "subarr.test:9922"}
    )
    assert r.status_code == 200


@pytest.mark.anyio
async def test_csrf_allows_headerless_clients():
    """curl / httpx / subgen's webhook send no Origin and no Sec-Fetch-* —
    they are not CSRF vectors and must pass untouched."""
    from subarr.api_security import CsrfOriginMiddleware

    c = _mini_app(CsrfOriginMiddleware, enabled=True)
    r = await _req(c, "POST", "/api/thing")
    assert r.status_code == 200


@pytest.mark.anyio
async def test_csrf_ignores_safe_methods_and_non_api():
    from subarr.api_security import CsrfOriginMiddleware

    c = _mini_app(CsrfOriginMiddleware, enabled=True)
    r = await _req(c, "GET", "/api/thing", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 200
    c2 = _mini_app(CsrfOriginMiddleware, enabled=True)
    r2 = await _req(c2, "GET", "/static/x.js", headers={"Sec-Fetch-Site": "cross-site"})
    assert r2.status_code == 200


@pytest.mark.anyio
async def test_csrf_opt_out():
    from subarr.api_security import CsrfOriginMiddleware

    c = _mini_app(CsrfOriginMiddleware, enabled=False)
    r = await _req(c, "POST", "/api/thing", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 200


# ─── API key gate ────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_api_key_disabled_is_noop():
    from subarr.api_security import ApiKeyMiddleware

    c = _mini_app(ApiKeyMiddleware, api_key="")
    r = await _req(c, "POST", "/api/thing")
    assert r.status_code == 200


@pytest.mark.anyio
async def test_api_key_required_when_set():
    from subarr.api_security import ApiKeyMiddleware

    c = _mini_app(ApiKeyMiddleware, api_key="sekrit123")
    r = await _req(c, "GET", "/api/thing")
    assert r.status_code == 401


@pytest.mark.anyio
async def test_api_key_header_accepted():
    from subarr.api_security import ApiKeyMiddleware

    c = _mini_app(ApiKeyMiddleware, api_key="sekrit123")
    r = await _req(c, "GET", "/api/thing", headers={"X-Api-Key": "sekrit123"})
    assert r.status_code == 200


@pytest.mark.anyio
async def test_api_key_query_param_accepted():
    """?apikey= exists so subgen's WEBHOOK_URL_COMPLETED can carry the key."""
    from subarr.api_security import ApiKeyMiddleware

    c = _mini_app(ApiKeyMiddleware, api_key="sekrit123")
    r = await _req(c, "GET", "/api/thing?apikey=sekrit123")
    assert r.status_code == 200


@pytest.mark.anyio
async def test_api_key_wrong_key_rejected():
    from subarr.api_security import ApiKeyMiddleware

    c = _mini_app(ApiKeyMiddleware, api_key="sekrit123")
    r = await _req(c, "GET", "/api/thing", headers={"X-Api-Key": "wrong"})
    assert r.status_code == 401


@pytest.mark.anyio
async def test_api_key_health_and_static_exempt():
    from subarr.api_security import ApiKeyMiddleware

    c = _mini_app(ApiKeyMiddleware, api_key="sekrit123")
    r = await _req(c, "GET", "/api/health")
    assert r.status_code == 200
    c2 = _mini_app(ApiKeyMiddleware, api_key="sekrit123")
    r2 = await _req(c2, "GET", "/static/x.js")
    assert r2.status_code == 200


# ─── UI bootstrap endpoint ───────────────────────────────────────────


def test_ui_bootstrap_same_origin_only(app_with_stub, monkeypatch):
    from subarr.config import settings

    object.__setattr__(settings, "api_key", "sekrit123")
    try:
        # Same-origin page script gets the key.
        r = app_with_stub.get("/api/ui-bootstrap", headers={"Sec-Fetch-Site": "same-origin"})
        assert r.status_code == 200
        assert r.json()["api_key"] == "sekrit123"
        # Cross-site / direct-navigation / header-less requests do not.
        for hdrs in ({"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "none"}, {}):
            r2 = app_with_stub.get("/api/ui-bootstrap", headers=hdrs)
            assert r2.status_code == 403, hdrs
    finally:
        object.__setattr__(settings, "api_key", "")


def test_ui_bootstrap_when_no_key(app_with_stub):
    r = app_with_stub.get("/api/ui-bootstrap", headers={"Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200
    assert r.json()["api_key"] == ""
