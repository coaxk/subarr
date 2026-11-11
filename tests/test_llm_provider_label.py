"""#616 follow-up: the LLM health entry and Home tile say which provider is active.

The entry keeps the name ``ollama`` (telemetry and ``ollama_probe_result`` key
on it); the new ``provider`` field lets the UI label it.
"""

from __future__ import annotations

import httpx
import pytest

_LOCAL = "http://unsloth:8000/v1"


@pytest.fixture
def openai_app(monkeypatch, request):
    def _build(base_url: str = _LOCAL, model: str = "m1"):
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_BASE_URL", base_url)
        monkeypatch.setenv("OPENAI_MODEL", model)
        monkeypatch.setenv("OPENAI_VISION", "off")
        monkeypatch.setenv("OPENAI_SERVER_TYPE", "auto")
        # The boot warm reads app.state.llm while these tests swap it.
        request.getfixturevalue("no_dashboard_warm")
        return request.getfixturevalue("app_with_stub")

    return _build


def _openai_client(handler):
    from subarr.integrations.openai_compat import OpenAICompatClient

    c = OpenAICompatClient(base_url=_LOCAL, api_key="", model="m1", vision="off", server_type="auto")
    c._client = httpx.AsyncClient(base_url=c._base_url, transport=httpx.MockTransport(handler))
    return c


def _models_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"data": [{"id": "m1"}]})


def _down(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, json={"error": {"message": "nope"}})


def _health_llm_entry(client) -> dict:
    r = client.get("/api/integrations/health")
    assert r.status_code == 200
    return next(e for e in r.json()["integrations"] if e["name"] == "ollama")


def test_health_entry_provider_ollama(app_with_stub):
    entry = _health_llm_entry(app_with_stub)
    assert entry["provider"] == "ollama"


@pytest.mark.parametrize("handler", [_models_ok, _down], ids=["online", "offline"])
def test_health_entry_provider_openai(openai_app, handler):
    client = openai_app()
    client.app.state.llm = _openai_client(handler)
    entry = _health_llm_entry(client)
    assert entry["name"] == "ollama"
    assert entry["provider"] == "openai"
    assert entry["online"] is (handler is _models_ok)


def test_health_entry_provider_openai_unconfigured(openai_app):
    client = openai_app(base_url="", model="")
    entry = _health_llm_entry(client)
    assert entry["configured"] is False
    assert entry["provider"] == "openai"


def test_other_health_entries_have_no_provider(app_with_stub):
    r = app_with_stub.get("/api/integrations/health")
    others = [e for e in r.json()["integrations"] if e["name"] != "ollama"]
    assert others
    assert all("provider" not in e for e in others)


@pytest.mark.asyncio
@pytest.mark.parametrize("handler,status", [(_models_ok, "ok"), (_down, "error")], ids=["ok", "error"])
async def test_home_tile_provider_openai(openai_app, handler, status):
    from subarr.routers.home import _integrations_block

    state = openai_app().app.state
    state.llm = _openai_client(handler)
    tile = next(t for t in await _integrations_block(state) if t["name"] == "ollama")
    assert tile["status"] == status
    assert tile["provider"] == "openai"


@pytest.mark.asyncio
async def test_home_tile_provider_openai_unconfigured(openai_app):
    from subarr.routers.home import _integrations_block

    state = openai_app(base_url="", model="").app.state
    tile = next(t for t in await _integrations_block(state) if t["name"] == "ollama")
    assert tile["status"] == "muted"
    assert tile["provider"] == "openai"


@pytest.mark.asyncio
async def test_home_tile_provider_ollama(app_with_stub):
    from subarr.routers.home import _integrations_block

    tile = next(t for t in await _integrations_block(app_with_stub.app.state) if t["name"] == "ollama")
    assert tile["provider"] == "ollama"
