"""LLM enrichment + GPU-idle gating tests."""
from __future__ import annotations

import httpx
import pytest


# ───── ISO parser ──────────────────────────────────────────────────────


def test_parse_iso_extracts_first_code(subarr_env):
    from subarr.enrichment import parse_iso
    assert parse_iso("ko") == "ko"
    assert parse_iso("Korean") is None  # not an ISO code
    assert parse_iso("ko (Korean)") == "ko"
    assert parse_iso("'ja'") == "ja"
    assert parse_iso("und") is None
    assert parse_iso("") is None
    assert parse_iso("eng") == "eng"  # 3-letter also accepted


# ───── health ──────────────────────────────────────────────────────────


def test_enrichment_health_ok(app_with_stub):
    r = app_with_stub.get("/api/enrichment/health")
    assert r.status_code == 200
    body = r.json()
    assert body["online"] is True
    assert body["model"] == "test-model"
    assert body["model_available"] is True


def _ollama_down(req: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("boom", request=req)


@pytest.mark.ollama_stub(handler=_ollama_down)
def test_enrichment_health_reports_offline(app_with_stub):
    r = app_with_stub.get("/api/enrichment/health")
    body = r.json()
    assert body["online"] is False


# ───── single enrichment ──────────────────────────────────────────────


def _ollama_returns_ko(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/api/tags":
        return httpx.Response(200, json={"models": [{"name": "test-model"}]})
    if req.url.path == "/api/generate":
        return httpx.Response(200, json={"response": "ko\n"})
    return httpx.Response(404)


def _subgen_idle(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/queue":
        return httpx.Response(200, json={
            "queued": [], "processing": [],
            "queued_count": 0, "processing_count": 0,
            "idle": True, "version": "2026.05.3",
        })
    return httpx.Response(404)


@pytest.mark.ollama_stub(handler=_ollama_returns_ko)
@pytest.mark.subgen(handler=_subgen_idle)
def test_enrich_lang_returns_iso_code(app_with_stub):
    r = app_with_stub.post(
        "/api/enrichment/lang",
        json={"canonical_path": "TV/Squid Game", "title": "Squid Game"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["iso_code"] == "ko"
    assert body["raw_response"] == "ko"
    assert body["cached"] is False

    # Second call should hit cache.
    r2 = app_with_stub.post(
        "/api/enrichment/lang",
        json={"canonical_path": "TV/Squid Game", "title": "Squid Game"},
    )
    assert r2.json()["cached"] is True


# ───── #156 structured JSON output ────────────────────────────────────


def _ollama_returns_json(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/api/tags":
        return httpx.Response(200, json={"models": [{"name": "test-model"}]})
    if req.url.path == "/api/generate":
        # Simulate a real Ollama JSON-mode response: the model returns a
        # serialised JSON object as a string in the `response` field.
        return httpx.Response(200, json={
            "response": '{"iso_code": "ko", "confidence": 0.93, "reasoning": "Squid Game is a Korean Netflix series."}',
        })
    return httpx.Response(404)


@pytest.mark.ollama_stub(handler=_ollama_returns_json)
@pytest.mark.subgen(handler=_subgen_idle)
def test_enrich_lang_parses_structured_output(app_with_stub):
    """#156: with format_schema set, Ollama returns JSON; we parse iso_code
    out of the structured payload AND surface confidence + reasoning."""
    r = app_with_stub.post(
        "/api/enrichment/lang",
        json={"canonical_path": "TV/Squid Game", "title": "Squid Game"},
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["iso_code"] == "ko"
    assert body["confidence"] == pytest.approx(0.93)
    assert "Korean" in (body["reasoning"] or "")


def _ollama_returns_und_json(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/api/tags":
        return httpx.Response(200, json={"models": [{"name": "test-model"}]})
    if req.url.path == "/api/generate":
        return httpx.Response(200, json={
            "response": '{"iso_code": "und", "confidence": 0.2, "reasoning": "Title is ambiguous."}',
        })
    return httpx.Response(404)


@pytest.mark.ollama_stub(handler=_ollama_returns_und_json)
@pytest.mark.subgen(handler=_subgen_idle)
def test_enrich_lang_und_normalised_to_null(app_with_stub):
    """'und' in the structured response must normalise to iso_code: null
    so downstream coverage scoring doesn't treat 'und' as a real language."""
    r = app_with_stub.post(
        "/api/enrichment/lang",
        json={"canonical_path": "TV/Ambiguous", "title": "Ambiguous"},
    )
    body = r.json()
    assert body["iso_code"] is None  # 'und' becomes null
    assert body["confidence"] == pytest.approx(0.2)


# ───── GPU-idle gating ────────────────────────────────────────────────


def _subgen_busy(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/queue":
        return httpx.Response(200, json={
            "queued": [],
            "processing": [{"path": "/media/TV/X/ep.mkv", "type": "transcribe"}],
            "queued_count": 0, "processing_count": 1,
            "idle": False, "version": "2026.05.3",
        })
    return httpx.Response(404)


@pytest.mark.subgen(handler=_subgen_busy)
@pytest.mark.ollama_stub(handler=_ollama_returns_ko)
def test_enrich_lang_gates_when_subgen_busy(app_with_stub):
    r = app_with_stub.post(
        "/api/enrichment/lang?gate=true",
        json={"canonical_path": "TV/Other", "title": "Other"},
    )
    assert r.status_code == 429
    assert "busy" in r.json()["detail"].lower()


@pytest.mark.subgen(handler=_subgen_busy)
@pytest.mark.ollama_stub(handler=_ollama_returns_ko)
def test_enrich_lang_gate_off_runs_even_when_busy(app_with_stub):
    r = app_with_stub.post(
        "/api/enrichment/lang?gate=false",
        json={"canonical_path": "TV/Other", "title": "Other"},
    )
    assert r.status_code == 200


# ───── bulk ───────────────────────────────────────────────────────────


@pytest.mark.subgen(handler=_subgen_idle)
@pytest.mark.ollama_stub(handler=_ollama_returns_ko)
def test_enrich_lang_bulk(app_with_stub):
    r = app_with_stub.post(
        "/api/enrichment/lang/bulk",
        json={"items": [
            {"canonical_path": "TV/A", "title": "A"},
            {"canonical_path": "TV/B", "title": "B"},
        ]},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["results"]) == 2
    assert all(x["iso_code"] == "ko" for x in body["results"])
    assert body["errors"] == []
