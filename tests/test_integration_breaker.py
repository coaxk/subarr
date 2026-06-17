"""#235: IntegrationClient honours its circuit breaker — a failing upstream
trips it and further calls short-circuit (no wasted request) until it recovers.
"""

from __future__ import annotations

import httpx
import pytest

from subarr.circuit_breaker import CircuitBreaker
from subarr.integrations import IntegrationError
from subarr.integrations.base import IntegrationClient


def _client(handler, breaker):
    c = IntegrationClient("http://up.test", breaker=breaker)
    c._client = httpx.AsyncClient(base_url="http://up.test", transport=httpx.MockTransport(handler))
    return c


@pytest.mark.asyncio
async def test_opens_after_threshold_then_short_circuits():
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("down")  # upstream unreachable

    cb = CircuitBreaker(fail_threshold=2, cooldown_s=60, clock=lambda: 0.0)
    c = _client(handler, cb)

    for _ in range(2):
        with pytest.raises(IntegrationError):
            await c._get("/x")
    assert cb.state == "open"
    assert calls["n"] == 2  # two real attempts

    # next call is short-circuited — the transport is NOT hit again
    with pytest.raises(IntegrationError, match="circuit open"):
        await c._get("/x")
    assert calls["n"] == 2  # unchanged — no wasted request


@pytest.mark.asyncio
async def test_5xx_trips_but_4xx_does_not():
    status = {"code": 503}

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(status["code"])

    cb = CircuitBreaker(fail_threshold=2, cooldown_s=60, clock=lambda: 0.0)
    c = _client(handler, cb)

    # two 5xx -> open
    for _ in range(2):
        with pytest.raises(IntegrationError):
            await c._get("/x")
    assert cb.state == "open"

    # reset with a fresh breaker; 4xx must NOT trip (upstream reachable)
    cb2 = CircuitBreaker(fail_threshold=2, cooldown_s=60, clock=lambda: 0.0)
    status["code"] = 404
    c2 = _client(handler, cb2)
    for _ in range(5):
        with pytest.raises(IntegrationError):
            await c2._get("/x")
    assert cb2.state == "closed"  # 404s never open the circuit


@pytest.mark.asyncio
async def test_recovers_after_cooldown_probe_succeeds():
    now = {"t": 0.0}
    up = {"ok": False}

    def handler(req: httpx.Request) -> httpx.Response:
        if not up["ok"]:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"ok": True})

    cb = CircuitBreaker(fail_threshold=1, cooldown_s=30, clock=lambda: now["t"])
    c = _client(handler, cb)

    with pytest.raises(IntegrationError):
        await c._get("/x")
    assert cb.state == "open"

    now["t"] += 31  # cooldown elapses, upstream back
    up["ok"] = True
    assert await c._get("/x") == {"ok": True}  # probe succeeds
    assert cb.state == "closed"
