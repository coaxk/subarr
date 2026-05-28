"""Tests for SubgenClient.probe_capabilities().

Covers the three real-world subgen builds subarr might be pointed at:

  1. subarr-subgen (our patched ghcr.io build) — /queue present + structured,
     version string parseable. is_subarr_subgen=True, no compat mode.

  2. Vanilla mccloud/subgen — /status works, /queue 404s.
     is_subarr_subgen=False, has_queue=False, has_batch=False.

  3. Unreachable — connection refused. Everything False.

Plus edge cases:
  - /queue returns 200 but with junk body (no queued/processing keys)
  - /status returns a non-standard version string
  - /status returns 5xx
"""

from __future__ import annotations

import json

import httpx
import pytest

from subarr.subgen_client import SubgenCapabilities, SubgenClient, SubgenUnavailable


def _make_client(mock_transport: httpx.MockTransport) -> SubgenClient:
    """Build a SubgenClient backed by a mocked transport."""
    c = SubgenClient(base_url="http://fake-subgen:9000")
    c._client = httpx.AsyncClient(
        base_url="http://fake-subgen:9000",
        transport=mock_transport,
    )
    return c


@pytest.mark.asyncio
async def test_patched_subgen_full_capabilities():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(200, json={
                "version": "Subgen 2026.05.3, stable-ts 0.7.0, faster-whisper 1.0.3 (docker)",
            })
        if request.url.path == "/queue":
            return httpx.Response(200, json={
                "queued": [], "processing": [],
                "queued_count": 0, "processing_count": 0,
                "idle": True, "version": "2026.05.3",
            })
        return httpx.Response(404)

    c = _make_client(httpx.MockTransport(handler))
    caps = await c.probe_capabilities()
    await c.aclose()

    assert caps.reachable is True
    assert caps.version == "2026.05.3"
    assert caps.has_queue is True
    assert caps.has_batch is True
    assert caps.is_subarr_subgen is True
    assert caps.to_dict()["compat_mode"] is False


@pytest.mark.asyncio
async def test_vanilla_subgen_compat_mode():
    """Vanilla subgen: /status works, /queue 404s."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(200, json={
                "version": "Subgen 2026.05.3, stable-ts 0.7.0, faster-whisper 1.0.3 (docker)",
            })
        if request.url.path == "/queue":
            return httpx.Response(404, text="Not Found")
        return httpx.Response(404)

    c = _make_client(httpx.MockTransport(handler))
    caps = await c.probe_capabilities()
    await c.aclose()

    assert caps.reachable is True
    assert caps.version == "2026.05.3"
    assert caps.has_queue is False
    assert caps.has_batch is False
    assert caps.is_subarr_subgen is False
    assert caps.to_dict()["compat_mode"] is True


@pytest.mark.asyncio
async def test_unreachable_subgen():
    """Network error → unreachable, everything False."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    c = _make_client(httpx.MockTransport(handler))
    caps = await c.probe_capabilities()
    await c.aclose()

    assert caps.reachable is False
    assert caps.version is None
    assert caps.has_queue is False
    assert caps.has_batch is False
    assert caps.is_subarr_subgen is False


@pytest.mark.asyncio
async def test_queue_200_but_junk_body_treated_as_missing():
    """If /queue returns 200 but the body lacks queued/processing keys,
    it's not our shape — treat as vanilla. Defensive against a future
    upstream that adds /queue with a different contract."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(200, json={"version": "Subgen 2026.99.0"})
        if request.url.path == "/queue":
            return httpx.Response(200, json={"hello": "world"})  # wrong shape
        return httpx.Response(404)

    c = _make_client(httpx.MockTransport(handler))
    caps = await c.probe_capabilities()
    await c.aclose()

    assert caps.reachable is True
    assert caps.version == "2026.99.0"
    assert caps.has_queue is False
    assert caps.is_subarr_subgen is False


@pytest.mark.asyncio
async def test_unparseable_version_doesnt_crash():
    """/status with a weird version string still returns reachable=True
    with version=None — version extraction is best-effort."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(200, json={"version": "unrecognised format"})
        if request.url.path == "/queue":
            return httpx.Response(404)
        return httpx.Response(404)

    c = _make_client(httpx.MockTransport(handler))
    caps = await c.probe_capabilities()
    await c.aclose()

    assert caps.reachable is True
    assert caps.version is None
    assert caps.has_queue is False


@pytest.mark.asyncio
async def test_status_5xx_returns_unreachable():
    """Subgen up but in error → treat as unreachable for capability purposes."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(503, text="overloaded")
        return httpx.Response(404)

    c = _make_client(httpx.MockTransport(handler))
    caps = await c.probe_capabilities()
    await c.aclose()

    assert caps.reachable is False


def test_capabilities_to_dict_includes_compat_mode():
    caps = SubgenCapabilities(
        reachable=True, version="2026.05.3",
        has_queue=False, has_batch=False, is_subarr_subgen=False,
    )
    d = caps.to_dict()
    assert d["compat_mode"] is True
    assert d["is_subarr_subgen"] is False


def test_unreachable_factory_helper():
    caps = SubgenCapabilities.unreachable()
    assert caps.reachable is False
    assert all(not getattr(caps, f) for f in ["has_queue", "has_batch", "is_subarr_subgen"])
