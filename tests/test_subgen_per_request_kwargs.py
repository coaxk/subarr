"""#88 subarr-side — per-request kwargs channel.

The v4.8 subgen patch lets POST /batch carry a per-request `kwargs` JSON
query param that overrides global + per-language SUBGEN_KWARGS for THIS scan
only (the tuning-lab / arena needs this so one config sweep doesn't require a
container restart per variant). subarr must:

  1. Detect the capability via /queue's capabilities.per_request_kwargs.
  2. Forward batch(kwargs={...}) as ?kwargs=<json> — and omit it entirely
     when not given (backward-compatible: vanilla/older subgen never sees it).
"""
from __future__ import annotations

import json

import httpx
import pytest

from subarr.subgen_client import SubgenClient


def _make_client(mock_transport: httpx.MockTransport) -> SubgenClient:
    c = SubgenClient(base_url="http://fake-subgen:9000")
    c._client = httpx.AsyncClient(
        base_url="http://fake-subgen:9000", transport=mock_transport,
    )
    return c


@pytest.mark.asyncio
async def test_capability_detected_from_queue_block():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(200, json={
                "version": "Subgen 2026.05.3, stable-ts 0.7.0 (docker)",
            })
        if request.url.path == "/queue":
            return httpx.Response(200, json={
                "queued": [], "processing": [],
                "capabilities": {"per_request_kwargs": True},
            })
        return httpx.Response(404)

    c = _make_client(httpx.MockTransport(handler))
    caps = await c.probe_capabilities()
    await c.aclose()

    assert caps.per_request_kwargs is True
    assert caps.to_dict()["per_request_kwargs"] is True


@pytest.mark.asyncio
async def test_capability_absent_defaults_false():
    """v4.7 and earlier: no per_request_kwargs flag → False, not crash."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(200, json={"version": "Subgen 2026.05.3 (docker)"})
        if request.url.path == "/queue":
            return httpx.Response(200, json={
                "queued": [], "processing": [],
                "capabilities": {"audio_language_override": True},
            })
        return httpx.Response(404)

    c = _make_client(httpx.MockTransport(handler))
    caps = await c.probe_capabilities()
    await c.aclose()

    assert caps.per_request_kwargs is False


@pytest.mark.asyncio
async def test_batch_forwards_kwargs_as_json_query_param():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"walked": 1, "queued": 1})

    c = _make_client(httpx.MockTransport(handler))
    await c.batch("/media/x", kwargs={"vad_filter": True, "beam_size": 5})
    await c.aclose()

    assert "kwargs" in seen["params"]
    assert json.loads(seen["params"]["kwargs"]) == {"vad_filter": True, "beam_size": 5}


@pytest.mark.asyncio
async def test_batch_omits_kwargs_when_not_given():
    """Backward-compat: no kwargs arg → no kwargs query param at all."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"walked": 0})

    c = _make_client(httpx.MockTransport(handler))
    await c.batch("/media/x")
    await c.aclose()

    assert "kwargs" not in seen["params"]


@pytest.mark.asyncio
async def test_batch_omits_kwargs_when_empty_dict():
    """Empty dict is a no-op override → don't send it (keeps the URL clean
    and lets subgen's global/per-language kwargs apply unmodified)."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"walked": 0})

    c = _make_client(httpx.MockTransport(handler))
    await c.batch("/media/x", kwargs={})
    await c.aclose()

    assert "kwargs" not in seen["params"]
