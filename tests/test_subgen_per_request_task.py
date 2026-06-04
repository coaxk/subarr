"""#131 subarr-side — per-request task channel.

The v4.9 subgen patch lets POST /batch carry a `task=transcribe|translate`
query param that overrides the global TRANSCRIBE_OR_TRANSLATE for one batch
(the tuning-lab arena drives a source-transcribe AND candidate-translate
through the same path-based channel). subarr must:

  1. Detect the capability via /queue's capabilities.per_request_task.
  2. Forward batch(task="transcribe"|"translate") as ?task= — omitting it
     entirely (and rejecting unknown values) so older subgen keeps its
     env-locked behaviour.
"""
from __future__ import annotations

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
            return httpx.Response(200, json={"version": "Subgen 2026.05.3 (docker)"})
        if request.url.path == "/queue":
            return httpx.Response(200, json={
                "queued": [], "processing": [],
                "capabilities": {"per_request_task": True},
            })
        return httpx.Response(404)

    c = _make_client(httpx.MockTransport(handler))
    caps = await c.probe_capabilities()
    await c.aclose()

    assert caps.per_request_task is True
    assert caps.to_dict()["per_request_task"] is True


@pytest.mark.asyncio
async def test_capability_absent_defaults_false():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(200, json={"version": "Subgen 2026.05.3 (docker)"})
        if request.url.path == "/queue":
            return httpx.Response(200, json={
                "queued": [], "processing": [],
                "capabilities": {"per_request_kwargs": True},
            })
        return httpx.Response(404)

    c = _make_client(httpx.MockTransport(handler))
    caps = await c.probe_capabilities()
    await c.aclose()

    assert caps.per_request_task is False


@pytest.mark.asyncio
async def test_asr_arena_capability_detected_from_queue_block():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(200, json={"version": "Subgen 2026.05.3 (docker)"})
        if request.url.path == "/queue":
            return httpx.Response(200, json={
                "queued": [], "processing": [],
                "capabilities": {"asr_arena": True},
            })
        return httpx.Response(404)

    c = _make_client(httpx.MockTransport(handler))
    caps = await c.probe_capabilities()
    await c.aclose()

    assert caps.asr_arena is True
    assert caps.to_dict()["asr_arena"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("task", ["transcribe", "translate"])
async def test_batch_forwards_valid_task(task):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"walked": 1, "queued": 1})

    c = _make_client(httpx.MockTransport(handler))
    await c.batch("/media/x", task=task)
    await c.aclose()

    assert seen["params"].get("task") == task


@pytest.mark.asyncio
async def test_batch_omits_task_when_not_given():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"walked": 0})

    c = _make_client(httpx.MockTransport(handler))
    await c.batch("/media/x")
    await c.aclose()

    assert "task" not in seen["params"]


@pytest.mark.asyncio
async def test_batch_rejects_unknown_task():
    """Defensive: an unknown task value is dropped, not forwarded (subgen
    would ignore it, but we keep the request clean + fail loud in code)."""
    c = _make_client(httpx.MockTransport(lambda r: httpx.Response(200, json={"walked": 0})))
    with pytest.raises(ValueError):
        await c.batch("/media/x", task="summarize")
    await c.aclose()
