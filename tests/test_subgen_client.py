import httpx
import pytest
from subarr.subgen_client import SubgenClient


def _client(handler):
    c = SubgenClient(base_url="http://fake:9000")
    c._client = httpx.AsyncClient(base_url="http://fake:9000", transport=httpx.MockTransport(handler))
    return c


@pytest.mark.asyncio
async def test_probe_reads_concurrent_transcriptions():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/queue":
            return httpx.Response(
                200,
                json={
                    "queued": [],
                    "processing": [],
                    "capabilities": {"concurrent_transcriptions": 3},
                },
            )
        return httpx.Response(200, json={"version": "Subgen 2026.05.3-r10, ..."})

    caps = await _client(handler).probe_capabilities()
    assert caps.concurrent_transcriptions == 3


@pytest.mark.asyncio
async def test_probe_concurrent_transcriptions_absent_is_none():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/queue":
            return httpx.Response(200, json={"queued": [], "processing": [], "capabilities": {}})
        return httpx.Response(200, json={"version": "Subgen 2026.05.3-r9, ..."})

    caps = await _client(handler).probe_capabilities()
    assert caps.concurrent_transcriptions is None
