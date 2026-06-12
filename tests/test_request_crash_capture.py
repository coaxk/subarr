"""#199 — unhandled request-handler exceptions feed the crash store.

Crash telemetry (#157 P2) covered supervised loops only; a 500 storm in
request handlers was invisible locally AND fleet-wide. This middleware
records (exc_type, module:line) for any exception that escapes a route,
then re-raises so FastAPI's normal 500 handling is untouched. Handled
HTTPExceptions never reach it (they resolve inside the routing app).
"""

from __future__ import annotations

import httpx
import pytest
from starlette.applications import Starlette
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import PlainTextResponse
from starlette.routing import Route


class _SpyStore:
    def __init__(self):
        self.recorded: list[BaseException] = []

    def record(self, exc):
        self.recorded.append(exc)


def _boom(request):
    raise ValueError("kaboom in a handler")


def _fine(request):
    return PlainTextResponse("ok")


def _http_404(request):
    raise StarletteHTTPException(status_code=404)


def _client(store):
    from subarr.request_crash_capture import RequestCrashCaptureMiddleware

    app = Starlette(
        routes=[
            Route("/api/boom", _boom),
            Route("/api/fine", _fine),
            Route("/api/notfound", _http_404),
        ]
    )
    wrapped = RequestCrashCaptureMiddleware(app, crash_recorder=store.record)
    transport = httpx.ASGITransport(app=wrapped, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


@pytest.mark.anyio
async def test_unhandled_exception_recorded_and_500(subarr_env):
    store = _SpyStore()
    async with _client(store) as c:
        r = await c.get("/api/boom")
    assert r.status_code == 500
    assert len(store.recorded) == 1
    assert type(store.recorded[0]).__name__ == "ValueError"


@pytest.mark.anyio
async def test_success_and_handled_http_errors_not_recorded(subarr_env):
    store = _SpyStore()
    async with _client(store) as c:
        ok = await c.get("/api/fine")
        nf = await c.get("/api/notfound")
    assert ok.status_code == 200
    assert nf.status_code == 404
    assert store.recorded == []


def test_real_app_records_handler_crash_to_crash_store(subarr_env, app_with_stub):
    """End-to-end through the real middleware stack: a route that raises
    lands a sanitized row in app.state.crashes (the lazy app.state lookup
    in app.py's recorder lambda is the seam under test)."""
    import time

    from fastapi.testclient import TestClient

    import subarr.app as app_mod

    @app_mod.app.get("/api/_test_boom_199")
    async def _boom_route():  # pragma: no cover - body raises immediately
        raise KeyError("induced for #199 test")

    try:
        with TestClient(app_mod.app, raise_server_exceptions=False) as c:
            before = (
                sum(c.app.state.crashes.counts_since(time.time() - 60).values())
                if hasattr(c.app.state, "crashes")
                else 0
            )
            r = c.get("/api/_test_boom_199")
            assert r.status_code == 500
            counts = c.app.state.crashes.counts_since(time.time() - 60)
            assert any(k.startswith("KeyError:") for k in counts), counts
            assert sum(counts.values()) == before + 1
    finally:
        app_mod.app.router.routes = [
            rt for rt in app_mod.app.router.routes if getattr(rt, "path", "") != "/api/_test_boom_199"
        ]


@pytest.mark.anyio
async def test_recorder_failure_never_masks_the_original_error(subarr_env):
    from subarr.request_crash_capture import RequestCrashCaptureMiddleware

    def bad_recorder(exc):
        raise RuntimeError("recorder is broken")

    app = Starlette(routes=[Route("/api/boom", _boom)])
    wrapped = RequestCrashCaptureMiddleware(app, crash_recorder=bad_recorder)
    transport = httpx.ASGITransport(app=wrapped, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/boom")
    # Original 500 behavior preserved; the broken recorder is swallowed.
    assert r.status_code == 500
