"""#364 slice 1 — subgen LID + translate adapters. Fake subgen: /asr returns
text (+ optional X-Detected-Language), /detect_language_robust returns the
robust aggregate. No real Whisper."""

from __future__ import annotations

import logging

import httpx
import pytest

from subarr.forced_segment_service import subgen_lid, subgen_translate
from subarr.subgen_client import SubgenClient


def _subgen(handler):
    c = SubgenClient(base_url="http://subgen.test:9000")
    c._client = httpx.AsyncClient(base_url="http://subgen.test:9000", transport=httpx.MockTransport(handler))
    return c


@pytest.mark.asyncio
async def test_translate_uploads_clip_and_returns_text(tmp_path):
    clip = tmp_path / "u.wav"
    clip.write_bytes(b"wavbytes")
    seen = {}

    def h(req):
        seen["path"] = req.url.path
        seen["task"] = req.url.params.get("task")
        seen["ct"] = req.headers.get("content-type", "")
        return httpx.Response(200, text="1\n00:00:00,000 --> 00:00:02,000\nHello\n")

    c = _subgen(h)
    text, lang = await subgen_translate(c, str(clip))
    await c.aclose()
    assert seen["path"] == "/asr" and seen["task"] == "translate"
    assert "multipart/form-data" in seen["ct"]
    assert "Hello" in text
    assert lang is None  # no X-Detected-Language header in this fake response


@pytest.mark.asyncio
async def test_lid_branch_a_path_visible_uses_detect_robust(tmp_path):
    seen = {}

    def h(req):
        seen["path"] = req.url.path
        seen["param_path"] = req.url.params.get("path")
        return httpx.Response(
            200,
            json={"aggregate": {"language": "fr", "n_agreeing": 3, "n_total": 3}, "chunks": []},
        )

    c = _subgen(h)
    lang, conf = await subgen_lid(c, str(tmp_path / "u.wav"), subgen_clip_path="/media-scratch/u.wav")
    await c.aclose()
    assert seen["path"] == "/detect_language_robust"
    assert seen["param_path"] == "/media-scratch/u.wav"
    assert lang == "fr" and conf == 1.0  # 3/3 agreeing


@pytest.mark.asyncio
async def test_lid_branch_b_upload_uses_asr_detected_language(tmp_path):
    clip = tmp_path / "u.wav"
    clip.write_bytes(b"wavbytes")
    seen = {}

    def h(req):
        seen["path"] = req.url.path
        seen["task"] = req.url.params.get("task")
        return httpx.Response(200, text="bonjour\n", headers={"X-Detected-Language": "fr"})

    c = _subgen(h)
    lang, conf = await subgen_lid(c, str(clip), subgen_clip_path=None)
    await c.aclose()
    assert seen["path"] == "/asr" and seen["task"] == "transcribe"
    assert lang == "fr" and conf == 1.0


@pytest.mark.asyncio
async def test_lid_none_confidence_when_detect_returns_nothing(tmp_path):
    def h(req):
        return httpx.Response(200, json={"aggregate": {}, "chunks": []})  # n_total 0 -> parse returns None

    c = _subgen(h)
    lang, conf = await subgen_lid(c, str(tmp_path / "u.wav"), subgen_clip_path="/media-scratch/u.wav")
    await c.aclose()
    assert lang is None and conf == 0.0


@pytest.mark.asyncio
async def test_branch_b_warns_once_about_cost(tmp_path, caplog):
    """DECISION (2026-07-09): Branch B (upload LID) is a degraded fallback and
    MUST emit a one-time cost WARNING per generator. A module-level guard fires
    it once; a second Branch-B call is silent."""
    import subarr.forced_segment_service as svc

    svc._warned_branch_b = False  # reset the module guard for a deterministic assert
    clip = tmp_path / "u.wav"
    clip.write_bytes(b"wavbytes")

    def h(req):
        return httpx.Response(200, text="bonjour\n", headers={"X-Detected-Language": "fr"})

    c = _subgen(h)
    with caplog.at_level(logging.WARNING, logger="subarr.forced_segment_service"):
        await subgen_lid(c, str(clip), subgen_clip_path=None)
        await subgen_lid(c, str(clip), subgen_clip_path=None)
    await c.aclose()
    warns = [r for r in caplog.records if "/asr upload path" in r.getMessage()]
    assert len(warns) == 1  # fired exactly once across two Branch-B calls


@pytest.mark.asyncio
async def test_branch_a_does_not_warn(tmp_path, caplog):
    import subarr.forced_segment_service as svc

    svc._warned_branch_b = False

    def h(req):
        return httpx.Response(
            200,
            json={"aggregate": {"language": "fr", "n_agreeing": 3, "n_total": 3}, "chunks": []},
        )

    c = _subgen(h)
    with caplog.at_level(logging.WARNING, logger="subarr.forced_segment_service"):
        await subgen_lid(c, str(tmp_path / "u.wav"), subgen_clip_path="/media-scratch/u.wav")
    await c.aclose()
    warns = [r for r in caplog.records if "/asr upload path" in r.getMessage()]
    assert len(warns) == 0  # Branch A never warns
