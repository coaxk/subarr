"""#616: credentials + Test connection for the OpenAI-compatible provider.

Covers the credential editor's new OpenAI/LLM entries, the masked-secret skip,
and the multi-step Test connection probe (`_test_openai`). The transport is
always an httpx.MockTransport swapped in on the client, as in
tests/test_llm_wiring.py.
"""

from __future__ import annotations

import base64
import json
import struct
import zlib

import httpx
import pytest

_LOCAL = "http://llm.test/v1"
_LANG_ANSWER = '{"iso_code": "ja", "confidence": 0.9, "reasoning": "r"}'


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


def _reply(content: str, *, finish_reason: str = "stop", **extra) -> dict:
    message: dict = {"content": content}
    message.update(extra)
    return {"choices": [{"message": message, "finish_reason": finish_reason}]}


def _models(*names: str) -> httpx.Response:
    return httpx.Response(200, json={"data": [{"id": n} for n in names]})


def _client(handler, *, base_url: str = _LOCAL, model: str = "m1", vision: str = "off", api_key: str = ""):
    from subarr.integrations.openai_compat import OpenAICompatClient

    c = OpenAICompatClient(
        base_url=base_url,
        api_key=api_key,
        model=model,
        vision=vision,
        server_type="auto",
    )
    c._client = httpx.AsyncClient(base_url=c._base_url, transport=httpx.MockTransport(handler))
    return c


def _seam(monkeypatch, handler, *, on_client=None) -> dict:
    """Patch _build_openai_test_client: build the real client, swap its
    transport for a recording MockTransport and return it."""
    import subarr.routers.onboarding as ob

    original = ob._build_openai_test_client
    seen: dict = {"body": None, "client": None, "paths": [], "auth": [], "bodies": []}

    def wrapper(body):
        client = original(body)
        seen["body"] = body
        seen["client"] = client

        def transport(request: httpx.Request) -> httpx.Response:
            seen["paths"].append(request.url.path)
            seen["auth"].append(request.headers.get("authorization"))
            seen["bodies"].append(json.loads(request.content) if request.content else None)
            return handler(request)

        client._client = httpx.AsyncClient(
            base_url=client._base_url,
            transport=httpx.MockTransport(transport),
        )
        if on_client is not None:
            on_client(client)
        return client

    monkeypatch.setattr(ob, "_build_openai_test_client", wrapper)
    return seen


def _good_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/models"):
        return _models("m1")
    body = json.loads(request.content)
    if isinstance(body["messages"][0]["content"], list):
        return httpx.Response(200, json=_reply("Red"))
    return httpx.Response(200, json=_reply(_LANG_ANSWER))


def _steps(result: dict) -> dict:
    return {step["name"]: step for step in result["steps"]}


# ─── 1-2. GET /api/integrations/{name}/config ───────────────────────


def test_openai_config_masks_key(app_with_stub, monkeypatch, set_setting):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    set_setting("openai_api_key", "abcd1234")

    r = app_with_stub.get("/api/integrations/openai/config")
    assert r.status_code == 200
    fields = r.json()["fields"]
    assert fields["api_key"]["value"] == "••••1234"
    assert fields["api_key"]["secret"] is True
    assert fields["api_key"]["has_value"] is True
    for key in ("url", "model", "vision", "server_type"):
        assert key in fields
        assert fields[key]["secret"] is False


def test_llm_config_lists_provider(app_with_stub):
    from subarr.config import settings

    r = app_with_stub.get("/api/integrations/llm/config")
    assert r.status_code == 200
    fields = r.json()["fields"]
    assert set(fields) == {"provider"}
    assert fields["provider"]["secret"] is False
    assert fields["provider"]["value"] == settings.llm_provider


# ─── 3-10. PUT /api/integrations/{name}/credentials ─────────────────


@pytest.mark.parametrize(
    "service,field,attr",
    [
        ("openai", "api_key", "openai_api_key"),
        ("sonarr", "api_key", "sonarr_api_key"),
        ("plex", "token", "plex_token"),
    ],
)
def test_save_skips_masked_secret(service, field, attr, app_with_stub, monkeypatch, set_setting):
    from subarr import config, config_store

    monkeypatch.delenv(config.FIELD_ENV_VARS[attr], raising=False)
    set_setting(attr, "abc")
    app = app_with_stub.app
    llm_before = app.state.llm
    integrations_before = app.state.integrations

    r = app_with_stub.put(f"/api/integrations/{service}/credentials", json={field: "••••abcd"})
    assert r.status_code == 200
    body = r.json()
    assert getattr(config.settings, attr) == "abc"
    assert attr not in config_store.load_overrides()
    assert body["skipped_masked"] == [attr]
    assert body["applied"] == []
    assert app.state.llm is llm_before
    assert app.state.integrations is integrations_before


def test_save_masked_key_with_other_fields(app_with_stub, monkeypatch, set_setting):
    from subarr.config import settings

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    set_setting("openai_api_key", "abc")

    r = app_with_stub.put(
        "/api/integrations/openai/credentials",
        json={"api_key": "••••", "model": "m2"},
    )
    assert r.status_code == 200
    body = r.json()
    assert settings.openai_api_key == "abc"
    assert settings.openai_model == "m2"
    assert body["applied"] == ["openai_model"]
    assert body["skipped_masked"] == ["openai_api_key"]


def test_save_empty_key_clears_it(app_with_stub, monkeypatch, set_setting):
    from subarr.config import settings

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    set_setting("openai_api_key", "abc")

    r = app_with_stub.put("/api/integrations/openai/credentials", json={"api_key": ""})
    assert r.status_code == 200
    assert settings.openai_api_key == ""
    assert "openai_api_key" in r.json()["applied"]


def test_save_provider_rebuilds_client(app_with_stub, monkeypatch, set_setting):
    from subarr import config_store
    from subarr.config import settings

    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    set_setting("llm_provider", "ollama")
    set_setting("openai_base_url", _LOCAL)
    set_setting("openai_model", "m1")
    app = app_with_stub.app

    r = app_with_stub.put("/api/integrations/llm/credentials", json={"provider": "openai"})
    assert r.status_code == 200
    assert type(app.state.llm).__name__ == "OpenAICompatClient"
    assert settings.llm_provider == "openai"
    assert config_store.load_overrides()["llm_provider"] == "openai"

    r = app_with_stub.put("/api/integrations/llm/credentials", json={"provider": "ollama"})
    assert r.status_code == 200
    assert app.state.llm.provider == "ollama"


def test_save_provider_is_normalised(app_with_stub, monkeypatch, set_setting):
    from subarr import config_store
    from subarr.config import settings

    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    set_setting("llm_provider", "ollama")
    set_setting("openai_base_url", _LOCAL)
    set_setting("openai_model", "m1")

    r = app_with_stub.put("/api/integrations/llm/credentials", json={"provider": " OpenAI "})
    assert r.status_code == 200
    assert settings.llm_provider == "openai"
    assert r.json()["applied"] == ["llm_provider"]
    assert config_store.load_overrides()["llm_provider"] == "openai"


def test_save_rejects_unknown_provider(app_with_stub, monkeypatch, set_setting):
    from subarr import config_store
    from subarr.config import settings

    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    set_setting("llm_provider", "ollama")

    r = app_with_stub.put("/api/integrations/llm/credentials", json={"provider": "bogus"})
    assert r.status_code == 400
    assert r.json()["detail"] == "provider must be ollama or openai"
    assert settings.llm_provider == "ollama"
    assert "llm_provider" not in config_store.load_overrides()


def test_save_rejects_unknown_server_type(app_with_stub, monkeypatch, set_setting):
    from subarr import config_store
    from subarr.config import settings

    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_SERVER_TYPE", raising=False)
    set_setting("openai_model", "m0")

    r = app_with_stub.put(
        "/api/integrations/openai/credentials",
        json={"model": "m9", "server_type": "azure"},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "server_type must be auto, local, openai or openrouter"
    assert settings.openai_model == "m0"
    assert "openai_model" not in config_store.load_overrides()


def test_save_openai_fields_persist(app_with_stub, monkeypatch, set_setting):
    from subarr import config_store
    from subarr.config import settings

    for var in ("OPENAI_BASE_URL", "OPENAI_MODEL", "OPENAI_VISION", "OPENAI_SERVER_TYPE"):
        monkeypatch.delenv(var, raising=False)
    for attr in ("openai_base_url", "openai_model", "openai_vision", "openai_server_type"):
        set_setting(attr, "")

    r = app_with_stub.put(
        "/api/integrations/openai/credentials",
        json={"url": "http://llm.test/v1", "model": "m1", "vision": "Same", "server_type": "LOCAL"},
    )
    assert r.status_code == 200
    overrides = config_store.load_overrides()
    expected = {
        "openai_base_url": "http://llm.test/v1",
        "openai_model": "m1",
        "openai_vision": "same",
        "openai_server_type": "local",
    }
    for attr, value in expected.items():
        assert getattr(settings, attr) == value
        assert overrides[attr] == value


def test_provider_env_managed(monkeypatch, request):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    client = request.getfixturevalue("app_with_stub")
    from subarr.config import settings

    r = client.put("/api/integrations/llm/credentials", json={"provider": "openai"})
    assert r.status_code == 200
    body = r.json()
    assert body["managed_by_env"] == ["llm_provider"]
    assert settings.llm_provider == "ollama"


# ─── 12-29. Test connection ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_test_openai_all_steps(subarr_env, monkeypatch):
    from subarr.routers.onboarding import TestRequest, _test_openai

    seen = _seam(monkeypatch, _good_handler)
    body = TestRequest(url=_LOCAL, api_key="abc", model="m1", vision="same")
    result = await _test_openai(body)

    assert result["ok"] is True
    assert [step["name"] for step in result["steps"]] == [
        "reachable",
        "key_accepted",
        "model_listed",
        "json_honoured",
        "thinking_off",
        "vision",
    ]
    assert all(step["ok"] for step in result["steps"])
    assert result["error"] is None
    assert result["detail"] == "6/6 checks passed"

    vision_bodies = [b for b in seen["bodies"] if b and isinstance(b["messages"][0]["content"], list)]
    assert len(vision_bodies) == 1
    image_url = vision_bodies[0]["messages"][0]["content"][1]["image_url"]["url"]
    assert image_url.startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_test_openai_no_vision_step_when_off(subarr_env, monkeypatch):
    from subarr.routers.onboarding import TestRequest, _test_openai

    seen = _seam(monkeypatch, _good_handler)
    result = await _test_openai(TestRequest(url=_LOCAL, api_key="abc", model="m1", vision="off"))

    assert [step["name"] for step in result["steps"]] == [
        "reachable",
        "key_accepted",
        "model_listed",
        "json_honoured",
        "thinking_off",
    ]
    assert result["detail"] == "5/5 checks passed"
    assert sum(1 for path in seen["paths"] if path.endswith("/chat/completions")) == 1


@pytest.mark.asyncio
async def test_test_openai_404_models_hint(subarr_env, monkeypatch):
    from subarr.routers.onboarding import TestRequest, _test_openai

    seen = _seam(monkeypatch, lambda request: httpx.Response(404, text="nope"))
    result = await _test_openai(TestRequest(url=_LOCAL, api_key="abc", model="m1"))

    steps = _steps(result)
    assert steps["reachable"]["ok"] is False
    assert "/v1" in steps["reachable"]["detail"]
    for name in ("key_accepted", "model_listed", "json_honoured", "thinking_off"):
        assert steps[name] == {"name": name, "ok": False, "detail": "not run"}
    assert result["ok"] is False
    assert result["error"] == steps["reachable"]["detail"]
    assert not any(path.endswith("/chat/completions") for path in seen["paths"])


@pytest.mark.asyncio
async def test_test_openai_unreachable(subarr_env, monkeypatch):
    from subarr.routers.onboarding import TestRequest, _test_openai

    def handler(request):
        raise httpx.ConnectError("refused")

    _seam(monkeypatch, handler)
    result = await _test_openai(TestRequest(url=_LOCAL, api_key="abc", model="m1"))

    steps = _steps(result)
    assert steps["reachable"]["ok"] is False
    for name in ("key_accepted", "model_listed", "json_honoured", "thinking_off"):
        assert steps[name]["detail"] == "not run"
        assert steps[name]["ok"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_test_openai_key_rejected(status, subarr_env, monkeypatch):
    from subarr.routers.onboarding import TestRequest, _test_openai

    seen = _seam(monkeypatch, lambda request: httpx.Response(status, text="denied"))
    result = await _test_openai(TestRequest(url=_LOCAL, api_key="abc", model="m1"))

    steps = _steps(result)
    assert steps["reachable"]["ok"] is True
    assert steps["key_accepted"]["ok"] is False
    for name in ("model_listed", "json_honoured", "thinking_off"):
        assert steps[name]["detail"] == "not run"
    assert not any(path.endswith("/chat/completions") for path in seen["paths"])


@pytest.mark.asyncio
async def test_test_openai_model_not_listed(subarr_env, monkeypatch):
    from subarr.routers.onboarding import TestRequest, _test_openai

    def handler(request):
        if request.url.path.endswith("/models"):
            return _models("m2")
        return httpx.Response(200, json=_reply(_LANG_ANSWER))

    _seam(monkeypatch, handler)
    result = await _test_openai(TestRequest(url=_LOCAL, api_key="abc", model="m1"))

    steps = _steps(result)
    assert steps["model_listed"]["ok"] is False
    assert steps["json_honoured"]["ok"] is True
    assert steps["thinking_off"]["ok"] is True


@pytest.mark.asyncio
async def test_test_openai_no_model(subarr_env, monkeypatch):
    from subarr.routers.onboarding import TestRequest, _test_openai

    seen = _seam(monkeypatch, _good_handler)
    result = await _test_openai(TestRequest(url=_LOCAL, api_key="abc", model="", vision="same"))

    steps = _steps(result)
    assert steps["model_listed"] == {"name": "model_listed", "ok": False, "detail": "no model set"}
    for name in ("json_honoured", "thinking_off", "vision"):
        assert steps[name]["detail"] == "not run"
    assert not any(path.endswith("/chat/completions") for path in seen["paths"])


@pytest.mark.asyncio
async def test_test_openai_wrong_language(subarr_env, monkeypatch):
    from subarr.routers.onboarding import TestRequest, _test_openai

    def handler(request):
        if request.url.path.endswith("/models"):
            return _models("m1")
        return httpx.Response(
            200,
            json=_reply('{"iso_code": "en", "confidence": 0.9, "reasoning": "r"}'),
        )

    _seam(monkeypatch, handler)
    result = await _test_openai(TestRequest(url=_LOCAL, api_key="abc", model="m1"))

    step = _steps(result)["json_honoured"]
    assert step["ok"] is False
    assert "'en'" in step["detail"]


@pytest.mark.asyncio
async def test_test_openai_prose_reply(subarr_env, monkeypatch):
    from subarr.routers.onboarding import TestRequest, _test_openai

    def handler(request):
        if request.url.path.endswith("/models"):
            return _models("m1")
        return httpx.Response(200, json=_reply("It is Japanese."))

    _seam(monkeypatch, handler)
    result = await _test_openai(TestRequest(url=_LOCAL, api_key="abc", model="m1"))

    step = _steps(result)["json_honoured"]
    assert step["ok"] is False
    assert step["detail"] == "the reply was not a JSON object with iso_code"


@pytest.mark.asyncio
async def test_test_openai_thinking_leak_reported(subarr_env, monkeypatch):
    from subarr.routers.onboarding import TestRequest, _test_openai

    def handler(request):
        if request.url.path.endswith("/models"):
            return _models("m1")
        body = json.loads(request.content)
        if isinstance(body["messages"][0]["content"], list):
            return httpx.Response(200, json=_reply("Red"))
        return httpx.Response(200, json=_reply(_LANG_ANSWER, reasoning="thinking..."))

    _seam(monkeypatch, handler)
    result = await _test_openai(TestRequest(url=_LOCAL, api_key="abc", model="m1", vision="same"))

    steps = _steps(result)
    assert steps["json_honoured"]["ok"] is True
    assert steps["thinking_off"]["ok"] is False
    assert steps["vision"]["detail"] == "answered red"


def test_test_openai_typed_key_wins(app_with_stub, monkeypatch, set_setting):
    set_setting("openai_api_key", "abc")
    seen = _seam(monkeypatch, _good_handler)

    r = app_with_stub.post(
        "/api/integrations/openai/test",
        json={"url": _LOCAL, "api_key": "xyz", "model": "m1"},
    )
    assert r.status_code == 200
    assert seen["auth"][0] == "Bearer xyz"


@pytest.mark.parametrize("api_key", ["••••", ""])
def test_test_openai_uses_stored_key_when_masked(api_key, app_with_stub, monkeypatch, set_setting):
    set_setting("openai_api_key", "abc")
    seen = _seam(monkeypatch, _good_handler)

    r = app_with_stub.post(
        "/api/integrations/openai/test",
        json={"url": _LOCAL, "api_key": api_key, "model": "m1"},
    )
    assert r.status_code == 200
    assert seen["auth"][0] == "Bearer abc"
    assert "abc" not in r.text


@pytest.mark.asyncio
async def test_test_openai_bypasses_learned_cache(subarr_env, monkeypatch):
    from subarr.routers.onboarding import TestRequest, _test_openai

    calls: list[dict] = []

    def on_client(client):
        original = client.generate

        async def spy(*args, **kwargs):
            calls.append(kwargs)
            return await original(*args, **kwargs)

        client.generate = spy

    _seam(monkeypatch, _good_handler, on_client=on_client)
    await _test_openai(TestRequest(url=_LOCAL, api_key="abc", model="m1", vision="off"))

    assert len(calls) == 1
    assert calls[0]["use_learned"] is False
    assert calls[0]["schema_name"] == "subarr_lang"
    assert calls[0]["num_predict"] == 120
    assert calls[0]["temperature"] == 0.0


@pytest.mark.asyncio
async def test_test_openai_key_never_in_response(subarr_env, monkeypatch):
    from subarr.routers.onboarding import TestRequest, _test_openai

    _seam(monkeypatch, lambda request: httpx.Response(500, text="echo: abc"))
    result = await _test_openai(TestRequest(url=_LOCAL, api_key="abc", model="m1"))

    assert "abc" not in json.dumps(result)


@pytest.mark.asyncio
async def test_test_openai_closes_client(subarr_env, monkeypatch):
    from subarr.routers.onboarding import TestRequest, _test_openai

    seen = _seam(monkeypatch, _good_handler)
    await _test_openai(TestRequest(url=_LOCAL, api_key="abc", model="m1"))
    assert seen["client"]._client.is_closed

    seen = _seam(monkeypatch, lambda request: httpx.Response(404, text="nope"))
    await _test_openai(TestRequest(url=_LOCAL, api_key="abc", model="m1"))
    assert seen["client"]._client.is_closed


def test_test_openai_does_not_touch_live_client(app_with_stub, monkeypatch):
    live_paths: list[str] = []

    def live_handler(request):
        live_paths.append(request.url.path)
        return httpx.Response(200, json={"data": []})

    live = _client(live_handler)
    app_with_stub.app.state.llm = live
    state_before = live.breaker.state
    failures_before = live.breaker._consecutive_failures

    _seam(monkeypatch, lambda request: httpx.Response(500, text="boom"))
    r = app_with_stub.post(
        "/api/integrations/openai/test",
        json={"url": _LOCAL, "api_key": "abc", "model": "m1"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert live_paths == []
    assert live.breaker.state == state_before
    assert live.breaker._consecutive_failures == failures_before


@pytest.mark.asyncio
async def test_test_openai_all_reasoning_reply(subarr_env, monkeypatch):
    from subarr.routers.onboarding import TestRequest, _test_openai

    def handler(request):
        if request.url.path.endswith("/models"):
            return _models("m1")
        body = json.loads(request.content)
        if isinstance(body["messages"][0]["content"], list):
            return httpx.Response(200, json=_reply("Red"))
        return httpx.Response(
            200,
            json=_reply("", finish_reason="length", reasoning="x"),
        )

    _seam(monkeypatch, handler)
    result = await _test_openai(TestRequest(url=_LOCAL, api_key="abc", model="m1", vision="same"))

    steps = _steps(result)
    assert steps["json_honoured"]["ok"] is False
    assert steps["thinking_off"]["ok"] is False
    assert steps["vision"]["ok"] is True


@pytest.mark.asyncio
async def test_test_openai_vision_wrong_answer(subarr_env, monkeypatch):
    from subarr.routers.onboarding import TestRequest, _test_openai

    def handler(request):
        if request.url.path.endswith("/models"):
            return _models("m1")
        body = json.loads(request.content)
        if isinstance(body["messages"][0]["content"], list):
            return httpx.Response(200, json=_reply("Blue"))
        return httpx.Response(200, json=_reply(_LANG_ANSWER))

    _seam(monkeypatch, handler)
    result = await _test_openai(TestRequest(url=_LOCAL, api_key="abc", model="m1", vision="same"))

    step = _steps(result)["vision"]
    assert step["ok"] is False
    assert "'Blue'" in step["detail"]


# ─── 30-33, 38. helpers + client flags ──────────────────────────────


def test_red_png_is_valid():
    from subarr.routers.onboarding import _red_png_b64

    data = base64.b64decode(_red_png_b64())
    assert data.startswith(b"\x89PNG\r\n\x1a\n")

    pos = 8
    chunks: list[tuple[bytes, bytes]] = []
    while pos < len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        ctype = data[pos + 4 : pos + 8]
        payload = data[pos + 8 : pos + 8 + length]
        crc = struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])[0]
        assert crc == zlib.crc32(ctype + payload) & 0xFFFFFFFF
        chunks.append((ctype, payload))
        pos += 12 + length

    assert chunks[0][0] == b"IHDR"
    width, height, depth, colour = struct.unpack(">IIBB", chunks[0][1][:10])
    assert (width, height, depth, colour) == (64, 64, 8, 2)
    idat = b"".join(payload for ctype, payload in chunks if ctype == b"IDAT")
    assert zlib.decompress(idat) == b"".join(b"\x00" + b"\xff\x00\x00" * 64 for _ in range(64))


@pytest.mark.asyncio
async def test_last_reply_had_reasoning():
    c = _client(lambda request: httpx.Response(200, json=_reply("hi")))
    assert c.last_reply_had_reasoning is None
    assert await c.generate("p") == "hi"
    assert c.last_reply_had_reasoning is False
    await c.aclose()

    c = _client(lambda request: httpx.Response(200, json=_reply("hi", reasoning_content="because")))
    await c.generate("p")
    assert c.last_reply_had_reasoning is True
    await c.aclose()

    c = _client(lambda request: httpx.Response(200, json=_reply('<think>a</think>{"iso_code": "ja"}')))
    await c.generate("p")
    assert c.last_reply_had_reasoning is True
    await c.aclose()

    responses = [
        httpx.Response(200, json=_reply("hi", reasoning="x")),
        httpx.Response(500, json={"error": {"message": "boom"}}),
    ]

    def handler(request):
        return responses.pop(0)

    c = _client(handler)
    await c.generate("p")
    assert c.last_reply_had_reasoning is True
    from subarr.integrations.llm import LLMError

    with pytest.raises(LLMError):
        await c.generate("p")
    assert c.last_reply_had_reasoning is None
    await c.aclose()


@pytest.mark.asyncio
async def test_models_404_message():
    from subarr.integrations.llm import LLMError

    c = _client(lambda request: httpx.Response(404, text="nope"))
    with pytest.raises(LLMError) as excinfo:
        await c.models()
    assert str(excinfo.value) == "no /v1/models here; the URL usually ends in /v1"
    assert excinfo.value.status == 404
    await c.aclose()

    c = _client(lambda request: httpx.Response(401, text="denied"))
    with pytest.raises(LLMError) as excinfo:
        await c.models()
    assert excinfo.value.status == 401
    await c.aclose()

    c = _client(lambda request: httpx.Response(500, text="boom"))
    with pytest.raises(LLMError) as excinfo:
        await c.models()
    assert excinfo.value.status == 500
    await c.aclose()

    def handler(request):
        raise httpx.ConnectError("refused")

    c = _client(handler)
    with pytest.raises(LLMError) as excinfo:
        await c.models()
    assert excinfo.value.status is None
    await c.aclose()


def test_llm_error_status_default():
    from subarr.integrations.llm import LLMError
    from subarr.integrations.ollama import OllamaError

    assert LLMError("x").status is None
    assert str(LLMError("x", status=5)) == "x"
    assert OllamaError("y").status is None


@pytest.mark.asyncio
async def test_last_reply_had_reasoning_empty_length():
    from subarr.integrations.llm import LLMError

    c = _client(lambda request: httpx.Response(200, json=_reply("", finish_reason="length")))
    with pytest.raises(LLMError):
        await c.generate("p")
    assert c.last_reply_had_reasoning is True
    await c.aclose()

    c = _client(lambda request: httpx.Response(200, json=_reply("partial", finish_reason="length")))
    assert await c.generate("p") == "partial"
    assert c.last_reply_had_reasoning is False
    await c.aclose()


# ─── 34-37. routing + request bodies ────────────────────────────────


def test_llm_test_endpoint_404(app_with_stub):
    r = app_with_stub.post("/api/integrations/llm/test", json={"url": "http://x"})
    assert r.status_code == 404


def test_onboarding_dispatches_openai(app_with_stub, monkeypatch):
    _seam(monkeypatch, _good_handler)

    r = app_with_stub.post(
        "/api/onboarding/test/openai",
        json={"url": _LOCAL, "api_key": "abc", "model": "m1"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["steps"]


@pytest.mark.asyncio
async def test_test_openai_vision_model_id(subarr_env, monkeypatch):
    from subarr.routers.onboarding import TestRequest, _test_openai

    seen = _seam(monkeypatch, _good_handler)
    await _test_openai(TestRequest(url=_LOCAL, api_key="abc", model="m1", vision="vis-1"))

    language_bodies = [b for b in seen["bodies"] if b and not isinstance(b["messages"][0]["content"], list)]
    vision_bodies = [b for b in seen["bodies"] if b and isinstance(b["messages"][0]["content"], list)]
    assert language_bodies[0]["model"] == "m1"
    assert vision_bodies[0]["model"] == "vis-1"


@pytest.mark.asyncio
async def test_test_openai_empty_model_list(subarr_env, monkeypatch):
    from subarr.routers.onboarding import TestRequest, _test_openai

    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json=_reply(_LANG_ANSWER))

    _seam(monkeypatch, handler)
    result = await _test_openai(TestRequest(url=_LOCAL, api_key="abc", model="m1"))

    steps = _steps(result)
    assert steps["reachable"]["ok"] is True
    assert steps["key_accepted"]["ok"] is True
    assert steps["model_listed"]["ok"] is False
    assert steps["model_listed"]["detail"] == "m1 is not in the server's list of 0 models"
    assert steps["json_honoured"]["ok"] is True
