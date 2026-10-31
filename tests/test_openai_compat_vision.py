"""#616: model listing and vision for the OpenAI-compatible client.

The chat transport is mocked with httpx.MockTransport driven by canned
responses (or handlers that raise), recording each request. The image host has
its own mock transport, patched in through `_image_fetch_transport`.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import traceback

import httpx
import pytest

from subarr.circuit_breaker import CircuitBreaker
from subarr.integrations import openai_compat
from subarr.integrations.llm import LLMError
from subarr.integrations.openai_compat import (
    LLMCoolingDown,
    OpenAICompatClient,
    _image_mime,
    reset_negotiation_cache,
)

_LOCAL = "http://unsloth:8000/v1"
_MODEL = "txt"
_KEY = "sk-SECRET-123456"
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
_PNG_B64 = base64.b64encode(_PNG).decode()
_PROMPT = "PROMPT-MARKER-7f3a"


@pytest.fixture(autouse=True)
def _clean_negotiation_cache():
    reset_negotiation_cache()
    yield
    reset_negotiation_cache()


@pytest.fixture(autouse=True)
def _quiet_httpx():
    """httpx logs every request URL at INFO; keep it out of caplog."""
    logger = logging.getLogger("httpx")
    old = logger.level
    logger.setLevel(logging.WARNING)
    yield
    logger.setLevel(old)


@pytest.fixture(autouse=True)
def slept(monkeypatch):
    """Replace `_sleep` with an immediate fake that records its delay."""
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(openai_compat, "_sleep", fake_sleep)
    return delays


@pytest.fixture
def now():
    return [0.0]


def _breaker(now) -> CircuitBreaker:
    return CircuitBreaker(name="llm", fail_threshold=5, cooldown_s=60.0, clock=lambda: now[0])


def _completion(content="ja", finish_reason="stop", **message_extra) -> dict:
    message = {"role": "assistant", "content": content}
    message.update(message_extra)
    return {"choices": [{"message": message, "finish_reason": finish_reason}]}


def _ok(content="ja") -> httpx.Response:
    return httpx.Response(200, json=_completion(content))


def _err(status: int, *, retry_after: str | None = None, text: str | None = None) -> httpx.Response:
    headers = {} if retry_after is None else {"retry-after": retry_after}
    if text is not None:
        return httpx.Response(status, text=text, headers=headers)
    return httpx.Response(status, json={"error": {"message": "nope"}}, headers=headers)


def _models_ok(*ids: str) -> httpx.Response:
    return httpx.Response(200, json={"object": "list", "data": [{"id": i} for i in ids]})


def _unsupported_temperature() -> httpx.Response:
    return httpx.Response(
        400,
        json={"error": {"message": "Unsupported parameter: 'temperature'", "param": "temperature"}},
    )


def _raises(exc: BaseException):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


class _Route:
    """MockTransport handler replaying canned items and recording requests."""

    def __init__(self, items=()):
        self.items = list(items)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.items.pop(0)
        return item(request) if callable(item) else item

    @property
    def calls(self) -> int:
        return len(self.requests)

    @property
    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


def _client(
    route, *, base_url=_LOCAL, model=_MODEL, vision="vis", server_type=None, api_key="k", breaker=None
):
    kwargs = {}
    if breaker is not None:
        kwargs["breaker"] = breaker
    c = OpenAICompatClient(
        base_url=base_url,
        api_key=api_key,
        model=model,
        vision=vision,
        server_type=server_type,
        **kwargs,
    )
    c._client = httpx.AsyncClient(base_url=c._base_url, transport=httpx.MockTransport(route))
    return c


def _image_route(monkeypatch, items) -> _Route:
    route = _Route(items)
    monkeypatch.setattr(openai_compat, "_image_fetch_transport", httpx.MockTransport(route))
    return route


# --- a. models() lists the ids -------------------------------------------


@pytest.mark.asyncio
async def test_models_lists_ids():
    chat = _Route([_models_ok("a", "b")])
    c = _client(chat, api_key=_KEY)
    assert await c.models() == ["a", "b"]
    assert chat.calls == 1
    request = chat.requests[0]
    assert request.method == "GET"
    assert request.url.path.endswith("/models")
    assert request.headers.get("authorization") == f"Bearer {_KEY}"


@pytest.mark.asyncio
async def test_models_without_key_sends_no_authorization():
    chat = _Route([_models_ok("a")])
    c = _client(chat, api_key="")
    assert await c.models() == ["a"]
    assert "authorization" not in chat.requests[0].headers


# --- b. models() skips bad items -----------------------------------------


@pytest.mark.asyncio
async def test_models_skips_bad_items():
    chat = _Route(
        [
            httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "a"},
                        "x",
                        {"id": 3},
                        {"id": ""},
                        {},
                        {"id": "b"},
                        {"id": "a"},
                    ]
                },
            )
        ]
    )
    c = _client(chat)
    assert await c.models() == ["a", "b", "a"]


@pytest.mark.asyncio
async def test_models_empty_data():
    chat = _Route([httpx.Response(200, json={"data": []})])
    c = _client(chat)
    assert await c.models() == []


# --- c. models() bad shapes ----------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json=[]),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"data": {"id": "a"}}),
        httpx.Response(200, json={"data": None}),
    ],
)
async def test_models_bad_shapes(response):
    chat = _Route([response])
    c = _client(chat)
    with pytest.raises(LLMError, match="no data list"):
        await c.models()


# --- d. models() non-json -------------------------------------------------


@pytest.mark.asyncio
async def test_models_non_json_has_no_cause():
    chat = _Route([httpx.Response(200, text=f"oops {_KEY}")])
    c = _client(chat, api_key=_KEY)
    with pytest.raises(LLMError) as excinfo:
        await c.models()
    exc = excinfo.value
    assert exc.__cause__ is None
    assert exc.__suppress_context__ is True
    assert _KEY not in str(exc)


# --- e. models() HTTP errors ---------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_models_auth_rejected(status):
    chat = _Route([_err(status, text=f"denied {_KEY}")])
    c = _client(chat, api_key=_KEY)
    with pytest.raises(LLMError) as excinfo:
        await c.models()
    message = str(excinfo.value)
    assert "API key rejected" in message
    assert _KEY not in message


@pytest.mark.asyncio
async def test_models_500_is_redacted():
    chat = _Route([_err(500, text=f"boom {_KEY}")])
    c = _client(chat, api_key=_KEY)
    with pytest.raises(LLMError) as excinfo:
        await c.models()
    message = str(excinfo.value)
    assert "500" in message
    assert "***" in message
    assert _KEY not in message


@pytest.mark.asyncio
async def test_models_transport_error_has_no_cause():
    chat = _Route([_raises(httpx.ConnectError(f"boom {_KEY}"))])
    c = _client(chat, api_key=_KEY)
    with pytest.raises(LLMError) as excinfo:
        await c.models()
    exc = excinfo.value
    assert exc.__cause__ is None
    assert _KEY not in str(exc)
    assert _KEY not in "".join(traceback.format_exception(exc))


# --- f. models() on a closed client --------------------------------------


@pytest.mark.asyncio
async def test_models_closed_client():
    closed = _raises(RuntimeError("Cannot send a request, as the client has been closed."))
    chat = _Route([closed])
    c = _client(chat)
    with pytest.raises(LLMError, match="client closed"):
        await c.models()


@pytest.mark.asyncio
async def test_models_other_runtime_error_propagates():
    chat = _Route([_raises(RuntimeError("other"))])
    c = _client(chat)
    with pytest.raises(RuntimeError) as excinfo:
        await c.models()
    assert not isinstance(excinfo.value, LLMError)


# --- g. models() ignores the breaker -------------------------------------


@pytest.mark.asyncio
async def test_models_ignores_open_breaker(now):
    chat = _Route([_models_ok("a")])
    breaker = _breaker(now)
    for _ in range(5):
        breaker.record_failure()
    assert breaker.state == "open"
    c = _client(chat, breaker=breaker)
    assert await c.models() == ["a"]
    assert chat.calls == 1
    assert breaker.state == "open"


@pytest.mark.asyncio
async def test_models_five_500s_leave_breaker_closed(now):
    chat = _Route([_err(500)] * 5 + [_ok()])
    breaker = _breaker(now)
    c = _client(chat, breaker=breaker)
    for _ in range(5):
        with pytest.raises(LLMError):
            await c.models()
    assert breaker.state == "closed"
    assert await c.generate("p") == "ja"
    assert chat.calls == 6


@pytest.mark.asyncio
async def test_models_200_does_not_reset_failure_count(now):
    chat = _Route([_models_ok("a")])
    breaker = _breaker(now)
    for _ in range(4):
        breaker.record_failure()
    c = _client(chat, breaker=breaker)
    assert await c.models() == ["a"]
    breaker.record_failure()
    assert breaker.state == "open"


@pytest.mark.asyncio
async def test_models_never_touches_breaker_methods(now):
    chat = _Route([_models_ok("a"), _err(500)])
    breaker = _breaker(now)
    breaker.record_failure()
    breaker.record_failure()
    allow_calls: list[int] = []
    success_calls: list[int] = []
    failure_calls: list[int] = []
    real_allow = breaker.allow
    real_success = breaker.record_success
    real_failure = breaker.record_failure

    def allow():
        allow_calls.append(1)
        return real_allow()

    def record_success():
        success_calls.append(1)
        return real_success()

    def record_failure():
        failure_calls.append(1)
        return real_failure()

    breaker.allow = allow
    breaker.record_success = record_success
    breaker.record_failure = record_failure
    c = _client(chat, breaker=breaker)
    assert await c.models() == ["a"]
    with pytest.raises(LLMError):
        await c.models()
    assert allow_calls == []
    assert success_calls == []
    assert failure_calls == []


# --- g2. models() never waits --------------------------------------------


@pytest.mark.asyncio
async def test_models_ignores_retry_after(slept):
    chat = _Route([_err(429, retry_after="1")])
    c = _client(chat)
    with pytest.raises(LLMError, match="429"):
        await c.models()
    assert chat.calls == 1
    assert slept == []


@pytest.mark.asyncio
async def test_models_does_not_follow_redirect(slept):
    chat = _Route([httpx.Response(302, headers={"location": "http://elsewhere/x"})])
    c = _client(chat)
    with pytest.raises(LLMError, match="302"):
        await c.models()
    assert chat.calls == 1


# --- h. models() configuration -------------------------------------------


@pytest.mark.asyncio
async def test_models_needs_a_base_url():
    chat = _Route([_models_ok("a")])
    c = _client(chat, base_url="")
    with pytest.raises(LLMError, match="not configured"):
        await c.models()
    assert chat.calls == 0


@pytest.mark.asyncio
async def test_models_works_without_a_model():
    chat = _Route([_models_ok("a", "b")])
    c = _client(chat, model="")
    assert await c.models() == ["a", "b"]
    assert chat.calls == 1


# --- i. vision_model ------------------------------------------------------


def _vision_client(vision: str, model: str = "m") -> OpenAICompatClient:
    return OpenAICompatClient(base_url=_LOCAL, api_key="k", model=model, vision=vision, server_type="local")


@pytest.mark.parametrize("vision", ["off", "", "OFF"])
def test_vision_model_off(vision):
    assert _vision_client(vision).vision_model is None


@pytest.mark.parametrize("vision", ["same", "Same"])
def test_vision_model_same(vision):
    assert _vision_client(vision, model="m").vision_model == "m"


def test_vision_model_same_without_text_model():
    assert _vision_client("same", model="").vision_model is None


def test_vision_model_keeps_case():
    assert _vision_client("Qwen2.5-VL-7B").vision_model == "Qwen2.5-VL-7B"


# --- j. resolve_vision_model and reset_vision_cache -----------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("vision,expected", [("off", None), ("same", "m"), ("vis-1", "vis-1")])
async def test_resolve_vision_model_matches_property(vision, expected):
    chat = _Route([])
    c = _client(chat, model="m", vision=vision)
    assert await c.resolve_vision_model() == expected
    assert await c.resolve_vision_model(installed=["x"]) == expected
    assert c.reset_vision_cache() is None
    assert chat.calls == 0


# --- k. vision off sends nothing -----------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("model_arg", [None, "llava"])
async def test_vision_off_raises_without_chat(monkeypatch, model_arg):
    chat = _Route([])
    image = _image_route(monkeypatch, [httpx.Response(200, content=_PNG)])
    c = _client(chat, vision="off")
    with pytest.raises(LLMError, match="vision not configured"):
        await c.vision_describe(image_b64=_PNG_B64, prompt="p", model=model_arg)
    assert chat.calls == 0
    assert image.calls == 0


@pytest.mark.asyncio
async def test_vision_off_never_fetches_url(monkeypatch, now):
    chat = _Route([])
    image = _image_route(monkeypatch, [httpx.Response(200, content=_PNG)])
    breaker, allow_calls = _counting_breaker(now)
    c = _client(chat, vision="off", breaker=breaker)
    with pytest.raises(LLMError, match="vision not configured"):
        await c.vision_describe(image_url="http://plex:32400/photo", prompt="p")
    assert image.calls == 0
    assert chat.calls == 0
    assert allow_calls == []


def _counting_breaker(now) -> tuple[CircuitBreaker, list[int]]:
    breaker = _breaker(now)
    allow_calls: list[int] = []
    real_allow = breaker.allow

    def allow():
        allow_calls.append(1)
        return real_allow()

    breaker.allow = allow
    return breaker, allow_calls


# --- l. unconfigured client ----------------------------------------------


@pytest.mark.asyncio
async def test_unconfigured_client_raises_model_text(monkeypatch):
    chat = _Route([])
    image = _image_route(monkeypatch, [httpx.Response(200, content=_PNG)])
    c = _client(chat, model="", vision="llava")
    with pytest.raises(LLMError) as excinfo:
        await c.vision_describe(image_url="http://plex:32400/photo", prompt="p")
    message = str(excinfo.value)
    assert "not configured" in message
    assert "vision not configured" not in message
    assert chat.calls == 0
    assert image.calls == 0


# --- m. model choice ------------------------------------------------------


@pytest.mark.asyncio
async def test_vision_same_uses_text_model():
    chat = _Route([_ok()])
    c = _client(chat, model="txt", vision="same")
    assert await c.vision_describe(image_b64=_PNG_B64, prompt="p") == "ja"
    assert chat.bodies[0]["model"] == "txt"


@pytest.mark.asyncio
async def test_vision_explicit_model():
    chat = _Route([_ok()])
    c = _client(chat, model="txt", vision="vis-1")
    assert await c.vision_describe(image_b64=_PNG_B64, prompt="p") == "ja"
    assert chat.bodies[0]["model"] == "vis-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("model_arg", ["vis-2", "", "   "])
async def test_vision_model_argument_wins(model_arg):
    chat = _Route([_ok()])
    c = _client(chat, model="txt", vision="vis-1")
    assert await c.vision_describe(image_b64=_PNG_B64, prompt="p", model=model_arg) == "ja"
    expected = "vis-2" if model_arg == "vis-2" else "vis-1"
    assert chat.bodies[0]["model"] == expected


# --- n. body shape per profile -------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "server_type,base_url",
    [
        ("local", "http://localhost:8080/v1"),
        ("openai", "https://api.openai.com/v1"),
        ("openrouter", "https://openrouter.ai/api/v1"),
    ],
)
async def test_vision_body_shape(server_type, base_url):
    chat = _Route([_ok(), _ok(), _ok()])
    c = _client(chat, base_url=base_url, server_type=server_type, model="txt", vision="vis")
    expected_url = "data:image/png;base64," + _PNG_B64
    assert await c.vision_describe(image_b64=_PNG_B64, prompt="p") == "ja"
    body = chat.bodies[0]
    assert body["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "p"},
                {"type": "image_url", "image_url": {"url": expected_url}},
            ],
        }
    ]
    assert body["temperature"] == 0.1
    assert "response_format" not in body
    cap = c._cap_field()
    assert body[cap] == 256
    assert await c.vision_describe(image_b64=_PNG_B64, prompt="p", num_predict=32) == "ja"
    assert chat.bodies[1][cap] == 32
    assert await c.generate("p") == "ja"
    gen_body = chat.bodies[2]
    knobs = ("reasoning_effort", "chat_template_kwargs", "reasoning")
    assert {k: body.get(k) for k in knobs} == {k: gen_body.get(k) for k in knobs}
    assert ("max_tokens" in body) == ("max_tokens" in gen_body)
    assert ("max_completion_tokens" in body) == ("max_completion_tokens" in gen_body)


# --- o. MIME detection ----------------------------------------------------


@pytest.mark.parametrize(
    "payload,mime",
    [
        (_PNG, "image/png"),
        (b"\xff\xd8\xff\xe0" + b"\x00" * 16, "image/jpeg"),
        (b"GIF89a" + b"\x00" * 16, "image/gif"),
        (b"GIF87a" + b"\x00" * 16, "image/gif"),
        (b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 8, "image/webp"),
        (b"hello world, not an image", "image/jpeg"),
    ],
)
def test_image_mime(payload, mime):
    assert _image_mime(base64.b64encode(payload).decode()) == mime


@pytest.mark.parametrize(
    "payload,mime",
    [
        (_PNG, "image/png"),
        (b"\xff\xd8\xff\xe0" + b"\x00" * 16, "image/jpeg"),
        (b"GIF89a" + b"\x00" * 16, "image/gif"),
        (b"GIF87a" + b"\x00" * 16, "image/gif"),
        (b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 8, "image/webp"),
        (b"hello world, not an image", "image/jpeg"),
    ],
)
@pytest.mark.asyncio
async def test_vision_data_uri_mime(payload, mime):
    chat = _Route([_ok()])
    c = _client(chat)
    b64 = base64.b64encode(payload).decode()
    assert await c.vision_describe(image_b64=b64, prompt="p") == "ja"
    assert chat.bodies[0]["messages"][0]["content"][1]["image_url"]["url"] == f"data:{mime};base64,{b64}"


@pytest.mark.asyncio
async def test_vision_non_base64_string_is_sent_unchanged():
    chat = _Route([_ok()])
    c = _client(chat)
    assert _image_mime("!!!!") == "image/jpeg"
    assert await c.vision_describe(image_b64="!!!!", prompt="p") == "ja"
    assert chat.bodies[0]["messages"][0]["content"][1]["image_url"]["url"].endswith(",!!!!")


# --- p. a URL is fetched server side -------------------------------------


@pytest.mark.asyncio
async def test_vision_url_fetched_server_side(monkeypatch):
    url = "http://plex:32400/photo?X-Plex-Token=SECRET"
    chat = _Route([_ok()])
    image = _image_route(monkeypatch, [httpx.Response(200, content=_PNG)])
    c = _client(chat)
    assert await c.vision_describe(image_url=url, prompt="p") == "ja"
    assert image.calls == 1
    assert image.requests[0].method == "GET"
    assert str(image.requests[0].url) == url
    assert "authorization" not in image.requests[0].headers
    expected_url = "data:image/png;base64," + _PNG_B64
    assert chat.bodies[0]["messages"][0]["content"][1]["image_url"]["url"] == expected_url
    raw = chat.requests[0].content.decode()
    assert "plex:32400" not in raw
    assert "SECRET" not in raw


# --- q. both given --------------------------------------------------------


@pytest.mark.asyncio
async def test_vision_b64_wins_over_url(monkeypatch):
    chat = _Route([_ok()])
    image = _image_route(monkeypatch, [httpx.Response(200, content=_PNG)])
    c = _client(chat)
    assert (
        await c.vision_describe(image_url="http://plex:32400/photo", image_b64=_PNG_B64, prompt="p") == "ja"
    )
    assert image.calls == 0
    assert chat.bodies[0]["messages"][0]["content"][1]["image_url"]["url"] == (
        "data:image/png;base64," + _PNG_B64
    )


# --- r. neither given -----------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{}, {"image_b64": "", "image_url": None}, {"image_url": ""}])
async def test_vision_needs_an_image(monkeypatch, kwargs):
    chat = _Route([])
    image = _image_route(monkeypatch, [httpx.Response(200, content=_PNG)])
    c = _client(chat)
    with pytest.raises(LLMError, match="needs image_url or image_b64"):
        await c.vision_describe(prompt="p", **kwargs)
    assert chat.calls == 0
    assert image.calls == 0


# --- s. fetch failures ----------------------------------------------------


@pytest.mark.asyncio
async def test_image_fetch_404(monkeypatch):
    chat = _Route([])
    _image_route(monkeypatch, [httpx.Response(404)])
    c = _client(chat)
    with pytest.raises(LLMError) as excinfo:
        await c.vision_describe(image_url="http://plex:32400/photo", prompt="p")
    assert str(excinfo.value) == "image fetch failed: HTTP 404"
    assert chat.calls == 0


@pytest.mark.asyncio
async def test_image_fetch_redirect_not_followed(monkeypatch):
    chat = _Route([])
    image = _image_route(
        monkeypatch,
        [httpx.Response(302, headers={"location": "http://elsewhere/x"})],
    )
    c = _client(chat)
    with pytest.raises(LLMError) as excinfo:
        await c.vision_describe(image_url="http://plex:32400/photo", prompt="p")
    assert str(excinfo.value) == "image fetch failed: HTTP 302 (redirects are not followed)"
    assert image.calls == 1
    assert chat.calls == 0


@pytest.mark.asyncio
async def test_image_fetch_empty_body(monkeypatch):
    chat = _Route([])
    _image_route(monkeypatch, [httpx.Response(200, content=b"")])
    c = _client(chat)
    with pytest.raises(LLMError) as excinfo:
        await c.vision_describe(image_url="http://plex:32400/photo", prompt="p")
    assert str(excinfo.value) == "image fetch returned no data"
    assert chat.calls == 0


@pytest.mark.asyncio
async def test_image_fetch_transport_error_is_masked(monkeypatch):
    url = "http://plex:32400/photo?X-Plex-Token=SECRET"
    chat = _Route([])
    _image_route(monkeypatch, [_raises(httpx.ConnectError(f"boom {url}"))])
    c = _client(chat)
    with pytest.raises(LLMError) as excinfo:
        await c.vision_describe(image_url=url, prompt="p")
    exc = excinfo.value
    assert str(exc) == "image fetch failed: ConnectError"
    assert exc.__cause__ is None
    assert "SECRET" not in "".join(traceback.format_exception(exc))
    assert chat.calls == 0


@pytest.mark.asyncio
async def test_image_fetch_value_error(monkeypatch):
    chat = _Route([])
    _image_route(monkeypatch, [_raises(ValueError("x"))])
    c = _client(chat)
    with pytest.raises(LLMError) as excinfo:
        await c.vision_describe(image_url="http://plex:32400/photo", prompt="p")
    assert str(excinfo.value) == "image fetch failed: ValueError"
    assert chat.calls == 0


# --- t. cancellation propagates ------------------------------------------


@pytest.mark.asyncio
async def test_image_fetch_cancellation_propagates(monkeypatch):
    chat = _Route([])
    _image_route(monkeypatch, [_raises(asyncio.CancelledError())])
    c = _client(chat)
    with pytest.raises(asyncio.CancelledError):
        await c.vision_describe(image_url="http://plex:32400/photo", prompt="p")
    assert chat.calls == 0


# --- u. vision shares the breaker ----------------------------------------


@pytest.mark.asyncio
async def test_vision_failures_open_the_breaker(now):
    chat = _Route([_err(500)] * 5 + [_ok()])
    breaker = _breaker(now)
    c = _client(chat, breaker=breaker)
    for _ in range(5):
        with pytest.raises(LLMError) as excinfo:
            await c.vision_describe(image_b64=_PNG_B64, prompt="p")
        assert not isinstance(excinfo.value, LLMCoolingDown)
    with pytest.raises(LLMCoolingDown):
        await c.generate("p")
    assert chat.calls == 5
    assert breaker.state == "open"


@pytest.mark.asyncio
async def test_vision_with_open_breaker_sends_nothing(now):
    chat = _Route([])
    breaker = _breaker(now)
    for _ in range(5):
        breaker.record_failure()
    c = _client(chat, breaker=breaker)
    with pytest.raises(LLMCoolingDown):
        await c.vision_describe(image_b64=_PNG_B64, prompt="p")
    assert chat.calls == 0


# --- v. fetch runs before the open breaker -------------------------------


@pytest.mark.asyncio
async def test_open_breaker_still_fetches_image(monkeypatch, now):
    chat = _Route([])
    image = _image_route(monkeypatch, [httpx.Response(200, content=_PNG)])
    breaker = _breaker(now)
    for _ in range(5):
        breaker.record_failure()
    c = _client(chat, breaker=breaker)
    with pytest.raises(LLMCoolingDown):
        await c.vision_describe(image_url="http://plex:32400/photo", prompt="p")
    assert image.calls == 1
    assert chat.calls == 0


# --- w. retry-after on vision --------------------------------------------


@pytest.mark.asyncio
async def test_vision_retry_after_then_success(slept):
    chat = _Route([_err(429, retry_after="1"), _ok()])
    c = _client(chat)
    assert await c.vision_describe(image_b64=_PNG_B64, prompt="p") == "ja"
    assert slept == [1.0]
    assert chat.calls == 2
    assert chat.bodies[0]["messages"] == chat.bodies[1]["messages"]


# --- x. reasoning retry on vision ----------------------------------------


@pytest.mark.asyncio
async def test_vision_reasoning_retry_raises_cap():
    reasoning = httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"role": "assistant", "content": "", "reasoning": "thinking"},
                    "finish_reason": "length",
                }
            ]
        },
    )
    chat = _Route([reasoning, _ok()])
    c = _client(chat)
    assert await c.vision_describe(image_b64=_PNG_B64, prompt="p") == "ja"
    cap = c._cap_field()
    assert chat.bodies[1][cap] == openai_compat._raised(256)


# --- y. the learned shape is per model -----------------------------------


@pytest.mark.asyncio
async def test_learned_shape_is_per_model():
    chat = _Route([_unsupported_temperature(), _ok(), _ok(), _ok()])
    c = _client(chat, model="txt", vision="vis")
    assert await c.vision_describe(image_b64=_PNG_B64, prompt="p") == "ja"
    assert "temperature" in chat.bodies[0]
    assert "temperature" not in chat.bodies[1]
    assert await c.generate("x") == "ja"
    assert "temperature" in chat.bodies[2]
    assert await c.vision_describe(image_b64=_PNG_B64, prompt="p") == "ja"
    assert "temperature" not in chat.bodies[3]
    assert chat.calls == 4


# --- z. logs name the effective model and carry no secrets ---------------


@pytest.mark.asyncio
async def test_negotiation_log_names_vision_model(caplog):
    caplog.set_level(logging.INFO)
    chat = _Route([_unsupported_temperature(), _ok()])
    c = _client(chat, model="txt", vision="vis")
    await c.vision_describe(image_b64=_PNG_B64, prompt=_PROMPT)
    messages = [r.getMessage() for r in caplog.records]
    assert any("rejected" in m and "vis" in m for m in messages)
    assert not any("txt" in m for m in messages)
    _assert_no_secrets(messages)


@pytest.mark.asyncio
async def test_retry_after_log_names_vision_model(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    url = "http://plex:32400/photo?X-Plex-Token=URLMARK"
    chat = _Route([_err(429, retry_after="1"), _ok()])
    image = _image_route(monkeypatch, [httpx.Response(200, content=_PNG)])
    c = _client(chat, model="txt", vision="vis")
    assert await c.vision_describe(image_url=url, prompt=_PROMPT) == "ja"
    assert image.calls == 1
    messages = [r.getMessage() for r in caplog.records]
    waits = [m for m in messages if "waiting" in m]
    assert len(waits) == 1
    assert "vis" in waits[0]
    assert not any("txt" in m for m in messages)
    _assert_no_secrets(messages)


@pytest.mark.asyncio
async def test_reasoning_retry_log_names_vision_model(caplog):
    caplog.set_level(logging.INFO)
    reasoning = httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"role": "assistant", "content": "", "reasoning": "thinking"},
                    "finish_reason": "length",
                }
            ]
        },
    )
    chat = _Route([reasoning, _ok()])
    c = _client(chat, model="txt", vision="vis")
    assert await c.vision_describe(image_b64=_PNG_B64, prompt=_PROMPT) == "ja"
    messages = [r.getMessage() for r in caplog.records]
    retries = [m for m in messages if "all reasoning" in m]
    assert len(retries) == 1
    assert "vis" in retries[0]
    _assert_no_secrets(messages)


def _assert_no_secrets(messages: list[str]) -> None:
    for message in messages:
        assert _PROMPT not in message
        assert _PNG_B64 not in message
        assert "plex:32400" not in message
        assert "URLMARK" not in message


# --- z2. chat errors on vision keep the text rules -----------------------


@pytest.mark.asyncio
async def test_vision_401_records_success(now):
    chat = _Route([_err(401)] + [_err(500)] * 5)
    breaker = _breaker(now)
    for _ in range(4):
        breaker.record_failure()
    c = _client(chat, breaker=breaker)
    with pytest.raises(LLMError, match="API key rejected"):
        await c.vision_describe(image_b64=_PNG_B64, prompt="p")
    assert breaker.state == "closed"
    breaker.record_failure()
    assert breaker.state == "closed"


# --- aa. the text path is unchanged --------------------------------------


@pytest.mark.asyncio
async def test_generate_still_uses_the_text_model():
    chat = _Route([_ok()])
    c = _client(chat, model="txt", vision="vis")
    assert await c.generate("p") == "ja"
    assert chat.bodies[0]["model"] == "txt"


# --- bb. reply parsing ----------------------------------------------------


@pytest.mark.asyncio
async def test_vision_strips_think_block():
    chat = _Route([_ok("<think>a</think>  red ")])
    c = _client(chat)
    assert await c.vision_describe(image_b64=_PNG_B64, prompt="p") == "red"
