"""#616: one active LLM client on app.state.llm, chosen by llm_provider."""

from __future__ import annotations

import logging

import httpx
import pytest

_LOCAL = "http://llm.test/v1"
_HOSTED = "https://api.openai.com/v1"


@pytest.fixture
def set_setting(subarr_env):
    from subarr import config

    saved: dict[str, object] = {}

    def _set(name: str, value: object) -> None:
        settings = config.settings
        if name not in saved:
            saved[name] = getattr(settings, name)
        object.__setattr__(settings, name, value)

    yield _set
    for name, value in saved.items():
        object.__setattr__(config.settings, name, value)


@pytest.fixture
def openai_app(monkeypatch, request):
    def _build(
        base_url: str = _LOCAL,
        model: str = "m1",
        vision: str = "same",
        server_type: str = "auto",
    ):
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_BASE_URL", base_url)
        monkeypatch.setenv("OPENAI_MODEL", model)
        monkeypatch.setenv("OPENAI_VISION", vision)
        monkeypatch.setenv("OPENAI_SERVER_TYPE", server_type)
        return request.getfixturevalue("app_with_stub")

    return _build


def _client(handler, *, base_url: str = _LOCAL, model: str = "m1", vision: str = "same"):
    from subarr.integrations.openai_compat import OpenAICompatClient

    c = OpenAICompatClient(
        base_url=base_url,
        api_key="",
        model=model,
        vision=vision,
        server_type="auto",
    )
    c._client = httpx.AsyncClient(base_url=c._base_url, transport=httpx.MockTransport(handler))
    return c


def _models_handler(paths: list[str], names: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"data": [{"id": n} for n in names]})

    return handler


def _chat_handler(paths: list[str], content: str):
    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]},
        )

    return handler


_LANG_ANSWER = '{"iso_code": "ja", "confidence": 0.9, "reasoning": "r"}'


# ───── factory ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("provider", ["ollama", "openai", "bogus"])
def test_factory_selects_provider(provider, set_setting, caplog):
    import asyncio

    from subarr.integrations.llm import build_llm_client

    set_setting("llm_provider", provider)
    if provider == "openai":
        set_setting("openai_base_url", _LOCAL)
        set_setting("openai_model", "m1")
        set_setting("openai_api_key", "abc")
        set_setting("openai_vision", "same")
        set_setting("openai_server_type", "auto")

    with caplog.at_level(logging.WARNING):
        c = build_llm_client()

    warnings = [
        r for r in caplog.records if r.name == "subarr.integrations.llm" and r.levelno == logging.WARNING
    ]
    if provider == "ollama":
        assert type(c).__name__ == "OllamaClient"
        assert c.provider == "ollama"
        assert warnings == []
    elif provider == "openai":
        assert type(c).__name__ == "OpenAICompatClient"
        assert c.provider == "openai"
        assert c.model == "m1"
        assert c.vision_model == "m1"
        assert c._headers() == {"Authorization": "Bearer abc"}
        assert warnings == []
    else:
        assert type(c).__name__ == "OllamaClient"
        assert len(warnings) == 1
        assert "bogus" in warnings[0].getMessage()
    asyncio.run(c.aclose())


def test_factory_passes_server_type(set_setting):
    import asyncio

    from subarr.integrations.llm import build_llm_client

    set_setting("llm_provider", "openai")
    set_setting("openai_base_url", _LOCAL)
    set_setting("openai_model", "m1")
    set_setting("openai_server_type", "openai")
    c = build_llm_client()
    assert c.profile == "openai"
    assert c.is_local_gpu is False
    asyncio.run(c.aclose())

    set_setting("openai_server_type", "auto")
    c = build_llm_client()
    assert c.profile == "local"
    asyncio.run(c.aclose())


# ───── startup + unconfigured openai ───────────────────────────────────


def test_openai_unconfigured_startup(openai_app):
    client = openai_app(base_url="", model="")

    r = client.get("/api/enrichment/health")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["online"] is False
    assert body["provider"] == "openai"

    r = client.post("/api/vision/check", json={"image_b64": "aGk="})
    assert r.status_code == 503

    r = client.post("/api/enrichment/lang", json={"canonical_path": "a/b.mkv", "title": "T"})
    assert r.status_code == 503
    assert r.json()["detail"] == "llm not configured"

    r = client.post(
        "/api/enrichment/lang/bulk",
        json={"items": [{"canonical_path": "a/b.mkv", "title": "T"}]},
    )
    assert r.status_code == 503

    r = client.get("/api/vision/status")
    assert r.status_code == 200
    body = r.json()
    assert body["vision_capable"] is False
    assert body["provider"] == "openai"


# ───── rebuild ─────────────────────────────────────────────────────────


class _FakeClient:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_rebuild_swaps_provider(set_setting):
    from types import SimpleNamespace

    from subarr.routers.onboarding import _rebuild_runtime_clients

    old_llm = _FakeClient()
    state = SimpleNamespace(integrations=_FakeClient(), subgen=_FakeClient(), llm=old_llm)

    set_setting("llm_provider", "openai")
    set_setting("openai_base_url", _LOCAL)
    set_setting("openai_model", "m1")
    await _rebuild_runtime_clients(state, reprobe=False)
    first = state.llm
    assert type(first).__name__ == "OpenAICompatClient"
    assert old_llm.closed

    set_setting("llm_provider", "ollama")
    await _rebuild_runtime_clients(state, reprobe=False)
    assert type(state.llm).__name__ == "OllamaClient"
    assert first._client.is_closed
    await state.llm.aclose()


@pytest.mark.asyncio
async def test_rebuild_ollama_to_unconfigured_openai(set_setting):
    from types import SimpleNamespace

    from subarr.integrations.ollama import OllamaClient
    from subarr.routers.onboarding import _rebuild_runtime_clients

    state = SimpleNamespace(integrations=_FakeClient(), subgen=_FakeClient(), llm=OllamaClient())
    set_setting("llm_provider", "openai")
    set_setting("openai_base_url", "")
    set_setting("openai_model", "")
    await _rebuild_runtime_clients(state, reprobe=False)
    assert state.llm.provider == "openai"
    assert state.llm.is_configured() is False
    await state.llm.aclose()


# ───── GPU gate ────────────────────────────────────────────────────────


def _busy_spy(monkeypatch):
    calls: list[int] = []

    async def spy(request):
        calls.append(1)
        return "subgen busy: 1"

    import subarr.routers.enrichment as r_enrichment

    monkeypatch.setattr(r_enrichment, "_gpu_busy", spy)
    return calls


def test_gpu_gate_skipped_for_hosted(openai_app, monkeypatch):
    client = openai_app()
    paths: list[str] = []
    client.app.state.llm = _client(_chat_handler(paths, _LANG_ANSWER), base_url=_HOSTED)
    calls = _busy_spy(monkeypatch)

    r = client.post(
        "/api/enrichment/lang?gate=true",
        json={"canonical_path": "a/b.mkv", "title": "T"},
    )
    assert r.status_code == 200
    assert r.json()["iso_code"] == "ja"
    assert calls == []


def test_gpu_gate_kept_for_local_profile(openai_app, monkeypatch):
    client = openai_app()
    paths: list[str] = []
    client.app.state.llm = _client(_chat_handler(paths, _LANG_ANSWER), base_url=_LOCAL)
    calls = _busy_spy(monkeypatch)

    r = client.post(
        "/api/enrichment/lang?gate=true",
        json={"canonical_path": "a/b.mkv", "title": "T"},
    )
    assert r.status_code == 429
    assert calls == [1]


def test_gpu_gate_kept_for_ollama(app_with_stub, monkeypatch):
    calls = _busy_spy(monkeypatch)

    r = app_with_stub.post(
        "/api/enrichment/lang?gate=true",
        json={"canonical_path": "a/b.mkv", "title": "T"},
    )
    assert r.status_code == 429
    assert calls == [1]


# ───── bulk loop ───────────────────────────────────────────────────────


def test_bulk_stops_when_cooling_down(openai_app):
    from subarr.integrations.openai_compat import LLMCoolingDown

    client = openai_app()
    llm = _client(_chat_handler([], _LANG_ANSWER), base_url=_HOSTED)
    client.app.state.llm = llm
    calls: list[int] = []

    async def fake_generate(prompt, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return _LANG_ANSWER
        if len(calls) == 2:
            raise LLMCoolingDown("x")
        raise AssertionError("generation continued after cooling down")

    llm.generate = fake_generate

    items = [{"canonical_path": f"show/{i}.mkv", "title": f"T{i}"} for i in range(4)]
    r = client.post("/api/enrichment/lang/bulk?gate=false", json={"items": items})
    assert r.status_code == 200
    body = r.json()
    assert len(body["results"]) == 1
    assert [e["canonical_path"] for e in body["errors"]] == [f"show/{i}.mkv" for i in (1, 2, 3)]
    assert all(e["error"] == "cooling down" for e in body["errors"])
    assert body["stopped"] == "cooling down"
    assert len(calls) == 2
    assert body["model_unloaded"] is False


def test_bulk_continues_after_ordinary_error(openai_app):
    from subarr.integrations.llm import LLMError

    client = openai_app()
    llm = _client(_chat_handler([], _LANG_ANSWER), base_url=_HOSTED)
    client.app.state.llm = llm
    calls: list[int] = []

    async def fake_generate(prompt, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise LLMError("boom")
        return _LANG_ANSWER

    llm.generate = fake_generate

    items = [{"canonical_path": f"show/{i}.mkv", "title": f"T{i}"} for i in range(4)]
    r = client.post("/api/enrichment/lang/bulk?gate=false", json={"items": items})
    assert r.status_code == 200
    body = r.json()
    assert len(body["results"]) == 3
    assert len(body["errors"]) == 1
    assert body["stopped"] is None
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_cooling_down_writes_no_store_row(tmp_path):
    from subarr.enrichment import EnrichmentStore, enrich_one
    from subarr.integrations.llm import LLMError
    from subarr.integrations.openai_compat import LLMCoolingDown
    from subarr.migrate import run_migrations

    class FakeLLM:
        model = "m1"

        def __init__(self, exc):
            self._exc = exc

        async def generate(self, prompt, **kwargs):
            raise self._exc

    db = tmp_path / "e.db"
    run_migrations(db)
    store = EnrichmentStore(db)

    path = "a/cool.mkv"
    with pytest.raises(LLMCoolingDown):
        await enrich_one(
            canonical_path=path,
            title="T",
            ollama=FakeLLM(LLMCoolingDown("x")),
            store=store,
        )
    assert store.get(path) is None

    path2 = "a/err.mkv"
    with pytest.raises(LLMError):
        await enrich_one(
            canonical_path=path2,
            title="T",
            ollama=FakeLLM(LLMError("boom")),
            store=store,
        )
    assert store.get(path2) is not None
    store.close()


def test_single_lang_cooling_down_is_503(openai_app):
    from subarr.integrations.openai_compat import LLMCoolingDown

    client = openai_app()
    llm = _client(_chat_handler([], _LANG_ANSWER), base_url=_HOSTED)
    client.app.state.llm = llm

    async def fake_generate(prompt, **kwargs):
        raise LLMCoolingDown("x")

    llm.generate = fake_generate

    r = client.post(
        "/api/enrichment/lang?gate=false",
        json={"canonical_path": "a/b.mkv", "title": "T"},
    )
    assert r.status_code == 503
    assert r.json()["detail"] == "cooling down"


# ───── unload ──────────────────────────────────────────────────────────


def test_unload_noop_for_openai(openai_app, monkeypatch):
    client = openai_app()
    client.app.state.llm = _client(_chat_handler([], _LANG_ANSWER), base_url=_HOSTED)
    calls: list[int] = []

    async def spy():
        calls.append(1)
        return {}

    import subarr.routers.gpu as r_gpu

    monkeypatch.setattr(r_gpu, "gpu_status", spy)

    r = client.post("/api/enrichment/unload")
    assert r.status_code == 200
    body = r.json()
    assert body["unloaded"] is False
    assert body["vram_before_mib"] is None
    assert calls == []


def test_unload_ollama_unchanged(app_with_stub):
    r = app_with_stub.post("/api/enrichment/unload")
    assert r.status_code == 200
    assert r.json()["unloaded"] is True


# ───── vision ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "vision,expected_capable,expected_resolved",
    [("off", False, None), ("same", True, "m1"), ("vis-1", True, "vis-1")],
)
def test_vision_status_openai(vision, expected_capable, expected_resolved, openai_app):
    client = openai_app()
    paths: list[str] = []
    client.app.state.llm = _client(_chat_handler(paths, _LANG_ANSWER), vision=vision)

    r = client.get("/api/vision/status")
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "openai"
    assert body["vision_capable"] is expected_capable
    assert body["vision_model_resolved"] == expected_resolved
    assert body["vision_model_config"] == vision
    assert "suggested_pull" not in body
    assert paths == []


def test_vision_status_ollama_has_provider(app_with_stub):
    r = app_with_stub.get("/api/vision/status")
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "ollama"
    assert "suggested_pull" in body


def test_vision_pull_rejected_for_openai(openai_app):
    client = openai_app()
    client.app.state.llm = _client(_chat_handler([], _LANG_ANSWER))
    r = client.post("/api/vision/pull", json={})
    assert r.status_code == 409
    assert r.json()["detail"] == "not supported by this provider"

    client.app.state.llm = _client(_chat_handler([], _LANG_ANSWER), base_url="", model="")
    r = client.post("/api/vision/pull", json={})
    assert r.status_code == 409
    assert r.json()["detail"] == "not supported by this provider"


def test_vision_no_client_is_503(app_with_stub):
    saved = app_with_stub.app.state.llm
    del app_with_stub.app.state.llm
    try:
        r = app_with_stub.post("/api/vision/pull", json={})
        assert r.status_code == 503
        assert r.json()["detail"] == "llm not configured"

        r = app_with_stub.post("/api/vision/check", json={"image_b64": "aGk="})
        assert r.status_code == 503
        assert r.json()["detail"] == "llm not configured"

        r = app_with_stub.get("/api/vision/status")
        assert r.status_code == 200
        body = r.json()
        assert body["provider"] == "ollama"
        assert body["vision_capable"] is False
    finally:
        app_with_stub.app.state.llm = saved


def test_vision_check_off_is_503(openai_app):
    client = openai_app()
    client.app.state.llm = _client(_chat_handler([], _LANG_ANSWER), vision="off")
    r = client.post("/api/vision/check", json={"image_b64": "aGk="})
    assert r.status_code == 503


def test_vision_check_other_error_is_502(openai_app):
    client = openai_app()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "nope"}})

    client.app.state.llm = _client(handler, vision="same")
    r = client.post("/api/vision/check", json={"image_b64": "aGk="})
    assert r.status_code == 502


# ───── health / probes / home ─────────────────────────────────────────


def test_health_uses_models_list(openai_app):
    client = openai_app()
    paths: list[str] = []
    llm = _client(_models_handler(paths, ["m1", "m2"]))
    client.app.state.llm = llm

    r = client.get("/api/enrichment/health")
    assert r.status_code == 200
    body = r.json()
    assert body["online"] is True
    assert body["provider"] == "openai"
    assert body["model_available"] is True
    assert body["installed_models"] == ["m1", "m2"]
    assert body["cooling_down"] is False
    assert "/chat/completions" not in paths

    for _ in range(5):
        llm.breaker.record_failure()

    r = client.get("/api/enrichment/health")
    assert r.json()["cooling_down"] is True

    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "nope"}})

    client.app.state.llm = _client(down)
    r = client.get("/api/enrichment/health")
    body = r.json()
    assert body["online"] is False
    assert "error" in body


def test_integrations_probe_openai(openai_app):
    client = openai_app()
    paths: list[str] = []
    client.app.state.llm = _client(_models_handler(paths, ["m1", "m2", "m3", "m4"]), vision="same")

    r = client.get("/api/integrations/health")
    assert r.status_code == 200
    entry = next(e for e in r.json()["integrations"] if e["name"] == "ollama")
    assert entry["online"] is True
    assert entry["badges"]["models"] == 4
    assert entry["badges"]["model_names"] == "m1, m2, m3 ..."
    assert entry["badges"]["vision_capable"] is True
    assert client.app.state.ollama_probe_result.reachable is False


def test_integrations_probe_ollama_sets_reachable(app_with_stub):
    r = app_with_stub.get("/api/integrations/health")
    assert r.status_code == 200
    assert app_with_stub.app.state.ollama_probe_result.reachable is True


@pytest.mark.asyncio
async def test_home_tile_openai(openai_app):
    from subarr.routers.home import _integrations_block

    client = openai_app()
    state = client.app.state

    paths: list[str] = []
    state.llm = _client(_models_handler(paths, ["m1", "m2"]))
    tiles = await _integrations_block(state)
    tile = next(t for t in tiles if t["name"] == "ollama")
    assert tile["status"] == "ok"
    assert tile["extra"] == "2 models · m1"

    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "nope"}})

    state.llm = _client(down)
    tiles = await _integrations_block(state)
    tile = next(t for t in tiles if t["name"] == "ollama")
    assert tile["status"] == "error"
    assert tile["extra"] == "unreachable"

    paths.clear()
    state.llm = _client(_models_handler(paths, ["m1"]), base_url="", model="")
    tiles = await _integrations_block(state)
    tile = next(t for t in tiles if t["name"] == "ollama")
    assert tile["status"] == "muted"
    assert tile["extra"] == "not configured"
    assert paths == []


# ───── no state.ollama left / default app ──────────────────────────────


def test_no_state_ollama_left():
    from pathlib import Path

    import subarr

    root = Path(subarr.__file__).resolve().parent
    forbidden = (
        "state.ollama =",
        "state.ollama.",
        'getattr(request.app.state, "ollama"',
        'getattr(app_.state, "ollama"',
        'getattr(state, "ollama"',
    )
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, f"{needle!r} found in {path}"


def test_default_app_holds_ollama_stub(app_with_stub):
    assert app_with_stub.app.state.llm.provider == "ollama"
    assert not hasattr(app_with_stub.app.state, "ollama")
