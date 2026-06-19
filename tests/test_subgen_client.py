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


@pytest.mark.asyncio
async def test_probe_concurrent_transcriptions_bool_is_none():
    """A subgen misconfig returning `true`/`false` must NOT parse as N=1/0 —
    isinstance(True, int) is True in Python, so the bool would sneak through the
    int guard and silently serialize the feeder. Bools map to None (gate off)."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/queue":
            return httpx.Response(
                200,
                json={"queued": [], "processing": [], "capabilities": {"concurrent_transcriptions": True}},
            )
        return httpx.Response(200, json={"version": "Subgen 2026.05.3-r10, ..."})

    caps = await _client(handler).probe_capabilities()
    assert caps.concurrent_transcriptions is None


@pytest.mark.asyncio
async def test_batch_normalizes_language_params_to_iso6391():
    """Arr tags are 3-letter ISO 639-2 ('fre'/'fra'/'ger'); normalize them to
    Whisper's 2-letter ISO 639-1 at the send boundary so subgen never has to
    guess (matches the strict /asr path). Unknown codes pass through unchanged."""
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(dict(req.url.params))
        return httpx.Response(200, json={})

    await _client(handler).batch("/media/library/x", audio_language_override="fre", force_language="ger")
    assert seen.get("audio_language_override") == "fr"
    assert seen.get("forceLanguage") == "de"
