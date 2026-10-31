"""#616: OpenAI-compatible client core — profiles, request shape, reply handling.

The transport is mocked with httpx.MockTransport (the same pattern as
tests/test_ollama_think.py::_capturing_client) and swapped in on the client,
so the Authorization header must be per-request rather than baked into the
shared AsyncClient. Everything here fails immediately on a non-200: parameter
negotiation, rate-limit retries and the breaker are later tasks.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager

import httpx
import pytest

from subarr.config import settings
from subarr.integrations.llm import LLMError, resolve_profile
from subarr.integrations.ollama import OllamaClient, OllamaError
from subarr.integrations.openai_compat import OpenAICompatClient, _redact


def _completion(content="ok", finish_reason="stop", **message_extra) -> dict:
    message = {"role": "assistant", "content": content}
    message.update(message_extra)
    return {"choices": [{"message": message, "finish_reason": finish_reason}]}


class _Recorder:
    """MockTransport handler that records path, headers and JSON body."""

    def __init__(self, factory=None):
        self.calls: list[dict] = []
        self._factory = factory or (lambda request: httpx.Response(200, json=_completion()))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(
            {
                "path": request.url.path,
                "headers": dict(request.headers),
                "json": json.loads(request.content) if request.content else None,
            }
        )
        return self._factory(request)

    @property
    def bodies(self) -> list[dict]:
        return [c["json"] for c in self.calls]


def _client(recorder: _Recorder, **kwargs) -> OpenAICompatClient:
    kwargs.setdefault("base_url", "http://unsloth:8000/v1")
    kwargs.setdefault("model", "m")
    c = OpenAICompatClient(**kwargs)
    c._client = httpx.AsyncClient(base_url=c._base_url, transport=httpx.MockTransport(recorder))
    return c


def _fixed(response: httpx.Response) -> _Recorder:
    return _Recorder(lambda request: response)


@contextmanager
def _settings(**values):
    """Settings is a frozen dataclass, so patch attributes via object.__setattr__."""
    old = {k: getattr(settings, k) for k in values}
    for k, v in values.items():
        object.__setattr__(settings, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            object.__setattr__(settings, k, v)


# --- a. resolve_profile ---------------------------------------------------


@pytest.mark.parametrize(
    "url,server_type,expected",
    [
        ("https://api.openai.com/v1", "auto", "openai"),
        ("https://API.OpenAI.com:443/v1", "auto", "openai"),
        ("https://openrouter.ai/api/v1", "auto", "openrouter"),
        ("https://eu.openrouter.ai/api/v1", "auto", "openrouter"),
        ("https://evilopenrouter.ai/v1", "auto", "local"),
        ("http://unsloth:8000/v1", "auto", "local"),
        ("http://proxy/v1", "openai", "openai"),
        ("https://api.openai.com/v1", "local", "local"),
        ("http://proxy/v1", "OpenRouter", "openrouter"),
        ("http://proxy/v1", "azure", "local"),
        ("", None, "local"),
        ("unsloth:8000/v1", "auto", "local"),
        ("http://x/v1", "", "local"),
    ],
)
def test_resolve_profile(url, server_type, expected):
    assert resolve_profile(url, server_type) == expected


# --- b-e. request bodies per profile --------------------------------------


@pytest.mark.asyncio
async def test_local_body():
    rec = _Recorder()
    c = _client(rec)
    await c.generate("p", num_predict=120)
    body = rec.bodies[0]
    assert body["max_tokens"] == 120
    assert body["temperature"] == 0.0
    assert body["reasoning_effort"] == "none"
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert "max_completion_tokens" not in body
    assert "reasoning" not in body
    assert "response_format" not in body
    assert "stream" not in body
    assert "keep_alive" not in body


@pytest.mark.asyncio
async def test_openrouter_body():
    rec = _Recorder()
    c = _client(rec, base_url="https://openrouter.ai/api/v1", api_key="k")
    await c.generate("p", num_predict=120)
    body = rec.bodies[0]
    assert body["max_tokens"] == 120
    assert body["reasoning"] == {"effort": "none"}
    assert "reasoning_effort" not in body
    assert "chat_template_kwargs" not in body
    assert "max_completion_tokens" not in body


@pytest.mark.asyncio
async def test_openai_body():
    rec = _Recorder()
    c = _client(rec, base_url="https://api.openai.com/v1", api_key="k")
    await c.generate("p", num_predict=120)
    body = rec.bodies[0]
    assert body["max_completion_tokens"] == 120
    assert body["reasoning_effort"] == "none"
    assert "max_tokens" not in body
    assert "chat_template_kwargs" not in body
    assert "reasoning" not in body


@pytest.mark.asyncio
async def test_exact_local_key_set():
    rec = _Recorder()
    c = _client(rec)
    await c.generate("p", num_predict=120)
    assert set(rec.bodies[0]) == {
        "model",
        "messages",
        "temperature",
        "max_tokens",
        "reasoning_effort",
        "chat_template_kwargs",
    }


@pytest.mark.asyncio
async def test_json_schema_shape():
    rec = _Recorder()
    c = _client(rec)
    schema = {"type": "object", "properties": {"iso_code": {"type": "string"}}}
    await c.generate("p", format_schema=schema, schema_name="subarr_lang")
    assert rec.bodies[0]["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "subarr_lang", "schema": schema, "strict": False},
    }


@pytest.mark.asyncio
async def test_json_schema_default_name():
    rec = _Recorder()
    c = _client(rec)
    await c.generate("p", format_schema={"type": "object"})
    assert rec.bodies[0]["response_format"]["json_schema"]["name"] == "subarr"


# --- g. messages ----------------------------------------------------------


@pytest.mark.asyncio
async def test_messages_with_system():
    rec = _Recorder()
    c = _client(rec)
    await c.generate("p", system="sys")
    assert rec.bodies[0]["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "p"},
    ]


@pytest.mark.asyncio
async def test_messages_without_system():
    rec = _Recorder()
    c = _client(rec)
    await c.generate("p")
    assert rec.bodies[0]["messages"] == [{"role": "user", "content": "p"}]


@pytest.mark.asyncio
async def test_messages_empty_system_omitted():
    rec = _Recorder()
    c = _client(rec)
    await c.generate("p", system="")
    assert rec.bodies[0]["messages"] == [{"role": "user", "content": "p"}]


# --- h. keep_alive is accepted but ignored --------------------------------


@pytest.mark.asyncio
async def test_keep_alive_not_sent():
    rec = _Recorder()
    c = _client(rec)
    await c.generate("p", keep_alive="30m")
    assert "keep_alive" not in rec.bodies[0]


# --- i. auth --------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_header_with_key():
    rec = _Recorder()
    c = _client(rec, api_key="k")
    await c.generate("p")
    # Present on the swapped client — i.e. it is a per-request header.
    assert rec.calls[0]["headers"]["authorization"] == "Bearer k"


@pytest.mark.asyncio
async def test_no_auth_header_with_empty_key():
    rec = _Recorder()
    c = _client(rec, api_key="")
    await c.generate("p")
    assert "authorization" not in rec.calls[0]["headers"]


@pytest.mark.asyncio
async def test_empty_key_beats_settings_key():
    with _settings(openai_api_key="sk-settings"):
        rec = _Recorder()
        c = _client(rec, api_key="")
        await c.generate("p")
    assert "authorization" not in rec.calls[0]["headers"]


@pytest.mark.asyncio
async def test_none_key_falls_back_to_settings():
    with _settings(openai_api_key="sk-settings"):
        rec = _Recorder()
        c = _client(rec, api_key=None)
        await c.generate("p")
    assert rec.calls[0]["headers"]["authorization"] == "Bearer sk-settings"


# --- j. base URL ----------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("base_url", ["http://x/v1/", "http://x/v1//", " http://x/v1 "])
async def test_trailing_slash_stripped(base_url):
    rec = _Recorder()
    c = _client(rec, base_url=base_url)
    await c.generate("p")
    assert rec.calls[0]["path"] == "/v1/chat/completions"


@pytest.mark.asyncio
async def test_no_v1_appended():
    rec = _Recorder()
    c = _client(rec, base_url="http://x")
    await c.generate("p")
    assert rec.calls[0]["path"] == "/chat/completions"


# --- k. is_configured -----------------------------------------------------


def test_configured_without_key():
    assert OpenAICompatClient(base_url="http://x/v1", model="m", api_key="").is_configured()


@pytest.mark.parametrize(
    "base_url,model",
    [("", "m"), ("http://x/v1", ""), ("http://x/v1", "   ")],
)
def test_not_configured(base_url, model):
    assert not OpenAICompatClient(base_url=base_url, model=model, api_key="").is_configured()


@pytest.mark.asyncio
async def test_unconfigured_generate_sends_nothing():
    rec = _Recorder()
    c = _client(rec, base_url="", model="m")
    with pytest.raises(LLMError, match="not configured"):
        await c.generate("p")
    assert rec.calls == []


# --- l. settings fallback -------------------------------------------------


def test_settings_fallback():
    with _settings(
        openai_base_url="http://api.openai.com/v1",
        openai_api_key="sk-settings",
        openai_model="gpt-5-mini",
        openai_vision="same",
        openai_server_type="auto",
    ):
        c = OpenAICompatClient()
    assert c._base_url == "http://api.openai.com/v1"
    assert c.model == "gpt-5-mini"
    assert c.profile == "openai"
    assert c._vision == "same"
    assert c.is_configured()


# --- m. timeouts ----------------------------------------------------------


@pytest.mark.parametrize(
    "base_url,connect",
    [
        ("http://unsloth:8000/v1", 3.0),
        ("https://api.openai.com/v1", 10.0),
        ("https://openrouter.ai/api/v1", 10.0),
    ],
)
def test_timeouts_per_profile(base_url, connect):
    c = OpenAICompatClient(base_url=base_url, model="m", api_key="k")
    assert c._client.timeout.connect == connect
    assert c._client.timeout.read == 120.0


def test_explicit_timeout_wins():
    c = OpenAICompatClient(base_url="http://unsloth:8000/v1", model="m", timeout=httpx.Timeout(1.0))
    assert c._client.timeout.connect == 1.0


# --- n. capability flags --------------------------------------------------


def test_capability_flags():
    assert OpenAICompatClient.supports_pull is False
    assert OpenAICompatClient.supports_unload is False
    assert OpenAICompatClient(base_url="http://unsloth:8000/v1", model="m").is_local_gpu is True
    assert OpenAICompatClient(base_url="https://api.openai.com/v1", model="m").is_local_gpu is False
    assert OpenAICompatClient(base_url="https://openrouter.ai/api/v1", model="m").is_local_gpu is False
    assert OllamaClient.supports_pull is True
    assert OllamaClient.supports_unload is True
    assert OllamaClient.is_local_gpu is True


# --- o. unload / aclose ---------------------------------------------------


@pytest.mark.asyncio
async def test_unload_sends_no_request():
    rec = _Recorder()
    c = _client(rec)
    await c.unload()
    assert rec.calls == []


@pytest.mark.asyncio
async def test_aclose_closes_client():
    rec = _Recorder()
    c = _client(rec)
    await c.aclose()
    assert c._client.is_closed


# --- p. reply handling: think blocks --------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content,expected",
    [
        ("  ja \n", "ja"),
        ("<think>abc</think>\nja", "ja"),
        ("\n<think>a</think> ja", "ja"),
        ("ja <think>x</think>", "ja <think>x</think>"),
    ],
)
async def test_think_handling(content, expected):
    c = _client(_fixed(httpx.Response(200, json=_completion(content))))
    assert await c.generate("p") == expected


@pytest.mark.asyncio
async def test_unclosed_think_with_length_is_reply_budget():
    c = _client(_fixed(httpx.Response(200, json=_completion("<think>abc", finish_reason="length"))))
    with pytest.raises(LLMError, match="reply budget"):
        await c.generate("p")


@pytest.mark.asyncio
async def test_think_only_with_stop_is_empty_reply():
    c = _client(_fixed(httpx.Response(200, json=_completion("<think>abc</think>", finish_reason="stop"))))
    with pytest.raises(LLMError, match="empty reply"):
        await c.generate("p")


# --- q. empty replies -----------------------------------------------------


@pytest.mark.asyncio
async def test_empty_length_raises_reply_budget():
    c = _client(_fixed(httpx.Response(200, json=_completion("", finish_reason="length"))))
    with pytest.raises(LLMError, match="reply budget"):
        await c.generate("p")


@pytest.mark.asyncio
async def test_none_content_length_raises_reply_budget():
    c = _client(_fixed(httpx.Response(200, json=_completion(None, finish_reason="length"))))
    with pytest.raises(LLMError, match="reply budget"):
        await c.generate("p")


@pytest.mark.asyncio
async def test_empty_stop_raises_empty_reply():
    c = _client(_fixed(httpx.Response(200, json=_completion("", finish_reason="stop"))))
    with pytest.raises(LLMError) as excinfo:
        await c.generate("p")
    assert "reply budget" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_empty_without_finish_reason_raises():
    c = _client(_fixed(httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})))
    with pytest.raises(LLMError):
        await c.generate("p")


@pytest.mark.asyncio
async def test_empty_with_reasoning_and_length_raises_after_one_request():
    rec = _fixed(
        httpx.Response(
            200,
            json=_completion("", finish_reason="length", reasoning="I thought about it"),
        )
    )
    c = _client(rec)
    with pytest.raises(LLMError, match="reply budget"):
        await c.generate("p")
    # No reasoning retry in this task.
    assert len(rec.calls) == 1


# --- r. malformed 200s ----------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json at all"),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{}]}),
    ],
)
async def test_malformed_replies_raise(response):
    c = _client(_fixed(response))
    with pytest.raises(LLMError):
        await c.generate("p")


# --- s. status handling ---------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,match,count",
    [
        (401, "API key rejected", 1),
        (403, "API key rejected", 1),
        (404, "model not found", 1),
        (400, "400", 1),
        (500, "500", 1),
        (429, "429", 1),
    ],
)
async def test_status_errors(status, match, count):
    rec = _fixed(httpx.Response(status, json={"error": {"message": "nope"}}))
    c = _client(rec, api_key="k")
    with pytest.raises(LLMError, match=match):
        await c.generate("p")
    assert len(rec.calls) == count


# --- t. key redaction -----------------------------------------------------

_KEY = "sk-SECRET-123456"


@pytest.mark.asyncio
async def test_key_redacted_in_500_body():
    body = f"bad request with {_KEY} and again {_KEY}"
    c = _client(_fixed(httpx.Response(500, text=body)), api_key=_KEY)
    with pytest.raises(LLMError) as excinfo:
        await c.generate("p")
    msg = str(excinfo.value)
    assert _KEY not in msg
    assert "***" in msg


@pytest.mark.asyncio
async def test_key_redacted_in_401_body():
    c = _client(_fixed(httpx.Response(401, text=f"invalid key {_KEY}")), api_key=_KEY)
    with pytest.raises(LLMError) as excinfo:
        await c.generate("p")
    assert _KEY not in str(excinfo.value)


@pytest.mark.asyncio
async def test_key_straddling_truncation_point():
    body = ("x" * 190) + _KEY + " trailing text"
    c = _client(_fixed(httpx.Response(500, text=body)), api_key=_KEY)
    with pytest.raises(LLMError) as excinfo:
        await c.generate("p")
    msg = str(excinfo.value)
    for i in range(len(_KEY) - 5):
        assert _KEY[i : i + 6] not in msg


@pytest.mark.asyncio
async def test_transport_error_redacted():
    def handler(request):
        raise httpx.ConnectError(f"connect failed with key {_KEY}")

    c = _client(_Recorder(handler), api_key=_KEY)
    with pytest.raises(LLMError) as excinfo:
        await c.generate("p")
    assert _KEY not in str(excinfo.value)


@pytest.mark.asyncio
async def test_key_never_logged(caplog):
    caplog.set_level(logging.DEBUG)
    cases = [
        _fixed(httpx.Response(500, text=f"{_KEY} {_KEY}")),
        _fixed(httpx.Response(401, text=f"echo {_KEY}")),
        _fixed(httpx.Response(500, text=("x" * 190) + _KEY + " tail")),
    ]

    def boom(request):
        raise httpx.ConnectError(f"boom {_KEY}")

    cases.append(_Recorder(boom))

    async def run(rec):
        c = _client(rec, api_key=_KEY)
        with pytest.raises(LLMError):
            await c.generate("p")

    for rec in cases:
        await run(rec)
    for record in caplog.records:
        assert _KEY not in record.getMessage()


# --- u. _redact -----------------------------------------------------------


def test_redact_empty_key_leaves_text_alone():
    assert _redact("abc", "") == "abc"


def test_redact_replaces_every_occurrence():
    assert _redact("xKEYyKEY", "KEY") == "x***y***"


# --- v. closed client / unrelated RuntimeError ----------------------------


@pytest.mark.asyncio
async def test_closed_client_raises_llm_error():
    c = OpenAICompatClient(base_url="http://x/v1", model="m", api_key="k")
    await c.aclose()
    with pytest.raises(LLMError, match="closed mid-request"):
        await c.generate("p")


@pytest.mark.asyncio
async def test_unrelated_runtime_error_reraised():
    def handler(request):
        raise RuntimeError("something else")

    c = _client(_Recorder(handler))
    with pytest.raises(RuntimeError) as excinfo:
        await c.generate("p")
    assert not isinstance(excinfo.value, LLMError)


# --- w. exception hierarchy -----------------------------------------------


def test_ollama_error_is_llm_error():
    assert issubclass(OllamaError, LLMError)
    assert issubclass(LLMError, RuntimeError)
