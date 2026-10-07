"""gemma4 support: thinking disabled on every /api/generate, gemma4 is a
known vision family, and the ISO parser survives trailing junk.

gemma4 THINKS by default and the reasoning tokens count against
num_predict, so a capped reply comes back EMPTY with done_reason "length".
Every /api/generate body must therefore send "think": false. Ollama
ignores it on non-thinking models (qwen2.5 verified), so it is safe to
send unconditionally.
"""

from __future__ import annotations

import json

import httpx
import pytest

from subarr.integrations.ollama import OllamaClient, _is_vision_capable


def _capturing_client(vision_model: str = "gemma4:12b-it-qat") -> tuple[OllamaClient, list[dict]]:
    """An OllamaClient whose transport records every /api/generate body."""
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/generate":
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"response": "ok", "done_reason": "stop"})
        return httpx.Response(404)

    c = OllamaClient(base_url="http://ollama:11434", model="gemma4:12b-it-qat", vision_model=vision_model)
    c._client = httpx.AsyncClient(base_url="http://ollama:11434", transport=httpx.MockTransport(handler))
    return c, bodies


@pytest.mark.asyncio
async def test_generate_sends_think_false():
    c, bodies = _capturing_client()
    await c.generate("hello", num_predict=8)
    assert len(bodies) == 1
    assert bodies[0]["think"] is False


@pytest.mark.asyncio
async def test_generate_with_schema_sends_think_false():
    c, bodies = _capturing_client()
    await c.generate("hello", format_schema={"type": "object"})
    assert bodies[0]["think"] is False


@pytest.mark.asyncio
async def test_vision_describe_sends_think_false():
    c, bodies = _capturing_client()
    await c.vision_describe(image_b64="aGVsbG8=", prompt="describe", model="gemma4:12b-it-qat")
    assert len(bodies) == 1
    assert bodies[0]["think"] is False
    assert bodies[0]["images"] == ["aGVsbG8="]


@pytest.mark.asyncio
async def test_unload_sends_think_false():
    c, bodies = _capturing_client()
    await c.unload()
    assert bodies[0]["think"] is False
    assert bodies[0]["keep_alive"] == 0


def test_gemma4_is_vision_capable():
    assert _is_vision_capable("gemma4:12b-it-qat") is True
    assert _is_vision_capable("gemma4:latest") is True
    # gemma3 is a different family and is not on the allowlist.
    assert _is_vision_capable("gemma3:4b") is False


@pytest.mark.asyncio
async def test_auto_pick_finds_gemma4_when_it_is_the_only_vision_model():
    c, _ = _capturing_client(vision_model="auto")

    async def installed():
        return ["gemma4:12b-it-qat", "nomic-embed-text:latest"]

    c.installed_models = installed
    assert await c.resolve_vision_model() == "gemma4:12b-it-qat"


@pytest.mark.asyncio
async def test_unconfigured_default_falls_back_to_gemma4():
    # Stock default vision model (qwen2.5vl:7b) removed, only gemma4 left.
    c, _ = _capturing_client(vision_model="qwen2.5vl:7b")

    async def installed():
        return ["gemma4:12b-it-qat"]

    c.installed_models = installed
    assert await c.resolve_vision_model() == "gemma4:12b-it-qat"


# --- ISO output hardening: gemma4 has been seen emitting "es</th>\n" ---


def test_parse_iso_strips_trailing_markup(subarr_env):
    from subarr.enrichment import parse_iso

    assert parse_iso("es</th>\n") == "es"


def test_structured_iso_field_with_trailing_markup(subarr_env):
    from subarr.enrichment import _parse_structured

    iso, conf, _ = _parse_structured('{"iso_code": "es</th>", "confidence": 0.9, "reasoning": "r"}')
    assert iso == "es"
    assert conf == 0.9


def test_structured_json_followed_by_junk(subarr_env):
    from subarr.enrichment import _parse_structured

    # Valid JSON then trailing junk: json.loads rejects the whole string, and
    # the free-text fallback used to take the first word, "iso", as a code.
    iso, conf, reasoning = _parse_structured(
        '{"iso_code": "es", "confidence": 0.8, "reasoning": "Spanish title"}</th>\n'
    )
    assert iso == "es"
    assert conf == 0.8
    assert reasoning == "Spanish title"
