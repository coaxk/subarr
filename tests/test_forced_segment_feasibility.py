"""#364 Task 0 — feasibility gate. Encodes the subgen-client contract the
forced-segment pipeline depends on: /asr accepts an UPLOADED clip (multipart),
/detect_language_robust is PATH-ONLY. If either contract changes, this fails
loudly so the pipeline's transport assumptions are revisited.

BRANCH DECISION (filled at execution 2026-07-10): the real client confirms both
channels. Task 7 uses Branch A (detect_language_robust on a shared clip path —
cheapest) when a subgen-visible scratch mount is available; otherwise it uses
Branch B (asr upload + return_language as LID). Translate always uploads via /asr.
"""

from __future__ import annotations

import httpx
import pytest

from subarr.subgen_client import SubgenClient


def _client(capture: dict):
    def handler(req: httpx.Request) -> httpx.Response:
        capture["path"] = req.url.path
        capture["params"] = dict(req.url.params)
        capture["content_type"] = req.headers.get("content-type", "")
        capture["body_len"] = len(req.content or b"")
        if req.url.path == "/asr":
            return httpx.Response(200, text="1\n00:00:00,000 --> 00:00:02,000\nhola\n")
        if req.url.path == "/detect_language_robust":
            return httpx.Response(
                200,
                json={
                    "aggregate": {"language": "es", "n_agreeing": 3, "n_total": 3},
                    "chunks": [],
                },
            )
        return httpx.Response(404)

    c = SubgenClient(base_url="http://subgen.test:9000")
    c._client = httpx.AsyncClient(base_url="http://subgen.test:9000", transport=httpx.MockTransport(handler))
    return c


@pytest.mark.asyncio
async def test_asr_uploads_a_local_clip_as_multipart(tmp_path):
    clip = tmp_path / "utt.wav"
    clip.write_bytes(b"RIFFfake-wav-bytes")
    cap: dict = {}
    c = _client(cap)
    text = await c.asr(local_file=str(clip), task="translate")
    await c.aclose()
    assert cap["path"] == "/asr"
    assert cap["params"].get("task") == "translate"
    assert "multipart/form-data" in cap["content_type"]  # the clip was UPLOADED
    assert cap["body_len"] > 0
    assert "hola" in text


@pytest.mark.asyncio
async def test_detect_language_robust_is_path_only_no_upload(tmp_path):
    cap: dict = {}
    c = _client(cap)
    resp = await c.detect_language_robust("/media-scratch/utt.wav")
    await c.aclose()
    assert cap["path"] == "/detect_language_robust"
    assert cap["params"].get("path") == "/media-scratch/utt.wav"  # server-visible path, not an upload
    assert "multipart/form-data" not in cap["content_type"]
    assert resp["aggregate"]["language"] == "es"
