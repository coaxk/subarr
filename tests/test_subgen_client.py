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


# ── #500: a 4xx from /detect_language_robust must not read as a result ───────
# The method gated on `status_code >= 500`, so subgen's 403 ("path is outside
# the allowed media root") and 400 ("path is required") were returned straight
# through as if they were detections. subarr answered 200 with an object
# carrying only `error`, and the Review UI, expecting chunks and a vote,
# rendered nothing. Reported by Jorman as "the detection button doesn't do
# anything". Every sibling method in this client already used `!= 200` or
# `>= 400`; this one was the outlier.


def _detect_handler(status: int, body: dict):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/detect_language_robust":
            return httpx.Response(status, json=body)
        return httpx.Response(404)

    return handler


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,body",
    [
        (403, {"error": "the path is outside the allowed media root"}),
        (400, {"error": "path is required"}),
        (404, {"detail": "Not Found"}),
    ],
)
async def test_detect_language_robust_raises_on_4xx(status, body):
    from subarr.subgen_client import SubgenUnavailable

    c = _client(_detect_handler(status, body))
    with pytest.raises(SubgenUnavailable) as ei:
        await c.detect_language_robust("/media/TV/x.avi")
    await c.aclose()
    # The status and subgen's own words must survive into the message, or the
    # operator is told "unavailable" with no way to find out why.
    assert str(status) in str(ei.value)


@pytest.mark.asyncio
async def test_detect_language_robust_still_returns_a_real_detection():
    c = _client(_detect_handler(200, {"detected_language": "en", "chunks": [], "confidence": 0.9}))
    out = await c.detect_language_robust("/media/TV/x.avi")
    await c.aclose()
    assert out["detected_language"] == "en"


@pytest.mark.asyncio
async def test_detect_language_robust_still_raises_on_5xx():
    from subarr.subgen_client import SubgenUnavailable

    c = _client(_detect_handler(503, {"error": "model loading"}))
    with pytest.raises(SubgenUnavailable):
        await c.detect_language_robust("/media/TV/x.avi")
    await c.aclose()
