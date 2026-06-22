"""#317 Slice B subarr-side — per-request ignore_forced channel.

The v4.15 subgen patch (0024) lets POST /batch carry an `ignore_forced=true`
query param that overrides the global IGNORE_FORCED_SUBTITLES for ONE batch,
so subarr's "transcribe a full sub anyway" action can fill a forced-only gap
without flipping a global knob. subarr must:

  1. Detect the capability via /queue's capabilities.request_ignore_forced.
  2. Forward batch(ignore_forced=True) as ?ignore_forced=true — omitting it
     when False so older subgen keeps its env-locked behaviour (and silently
     drops the unknown param).
"""

from __future__ import annotations

import httpx
import pytest

from subarr.subgen_client import SubgenClient


def _make_client(mock_transport: httpx.MockTransport) -> SubgenClient:
    c = SubgenClient(base_url="http://fake-subgen:9000")
    c._client = httpx.AsyncClient(
        base_url="http://fake-subgen:9000",
        transport=mock_transport,
    )
    return c


@pytest.mark.asyncio
async def test_request_ignore_forced_capability_detected():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(200, json={"version": "Subgen 2026.05.3 (docker)"})
        if request.url.path == "/queue":
            return httpx.Response(
                200,
                json={
                    "queued": [],
                    "processing": [],
                    "capabilities": {"request_ignore_forced": True},
                },
            )
        return httpx.Response(404)

    c = _make_client(httpx.MockTransport(handler))
    caps = await c.probe_capabilities()
    await c.aclose()

    assert caps.request_ignore_forced is True
    assert caps.to_dict()["request_ignore_forced"] is True


@pytest.mark.asyncio
async def test_request_ignore_forced_absent_defaults_false():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(200, json={"version": "Subgen 2026.05.3 (docker)"})
        if request.url.path == "/queue":
            return httpx.Response(
                200,
                json={
                    "queued": [],
                    "processing": [],
                    "capabilities": {"per_request_task": True},
                },
            )
        return httpx.Response(404)

    c = _make_client(httpx.MockTransport(handler))
    caps = await c.probe_capabilities()
    await c.aclose()

    assert caps.request_ignore_forced is False
    assert caps.to_dict()["request_ignore_forced"] is False


@pytest.mark.asyncio
async def test_batch_forwards_ignore_forced_when_true():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"walked": 1, "queued": 1})

    c = _make_client(httpx.MockTransport(handler))
    await c.batch("/media/x", ignore_forced=True)
    await c.aclose()

    assert seen["params"].get("ignore_forced") == "true"


@pytest.mark.asyncio
async def test_batch_omits_ignore_forced_by_default():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"walked": 0})

    c = _make_client(httpx.MockTransport(handler))
    await c.batch("/media/x")
    await c.aclose()

    assert "ignore_forced" not in seen["params"]
