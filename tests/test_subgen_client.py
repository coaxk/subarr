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


# === Fix C: /asr 4xx/5xx non-JSON error bodies must raise, not return as text ===


@pytest.mark.asyncio
async def test_asr_503_text_plain_raises_subgen_unavailable():
    """#288 Fix C: a non-JSON error body (proxy 5xx, text/plain) must raise
    SubgenUnavailable, not be silently returned as subtitle text."""
    from subarr.subgen_client import SubgenUnavailable

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            content=b"Service Unavailable",
            headers={"content-type": "text/plain"},
        )

    with pytest.raises(SubgenUnavailable, match="503"):
        await _client(handler).asr(path="/media/TV/ep.mkv")


@pytest.mark.asyncio
async def test_asr_200_text_plain_srt_still_returns_text():
    """#288 Fix C: a normal 200 text/plain SRT response must still be returned
    as text (not raised); empty string is a valid 200 result."""
    srt_body = "1\n00:00:01,000 --> 00:00:02,000\nHello world\n"

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=srt_body.encode(),
            headers={"content-type": "text/plain; charset=utf-8"},
        )

    result = await _client(handler).asr(path="/media/TV/ep.mkv")
    assert result == srt_body


@pytest.mark.asyncio
async def test_asr_200_empty_still_returns_empty_string():
    """#288 Fix C: empty 200 is a valid 'nothing produced' result; must not raise."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"", headers={"content-type": "text/plain"})

    result = await _client(handler).asr(path="/media/TV/ep.mkv")
    assert result == ""


# === Fix D: status() unguarded json must raise SubgenUnavailable on HTML body ===


@pytest.mark.asyncio
async def test_status_non_json_200_raises_subgen_unavailable():
    """#288 Fix D: status() must wrap JSONDecodeError in SubgenUnavailable.
    An HTML error page on a 200 (e.g. from a reverse proxy) currently raises
    a raw ValueError — wrap it so callers get the expected exception type."""
    from subarr.subgen_client import SubgenUnavailable

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"<html><body>Bad Gateway</body></html>",
            headers={"content-type": "text/html"},
        )

    with pytest.raises(SubgenUnavailable):
        await _client(handler).status()
