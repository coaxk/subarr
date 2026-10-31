"""#616: parameter negotiation and the reasoning retry.

The transport is mocked with httpx.MockTransport, driven by a list of canned
responses like tests/test_openai_compat_client.py.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from subarr.integrations.llm import LLMError
from subarr.integrations.openai_compat import (
    _SHAPES,
    OpenAICompatClient,
    reset_negotiation_cache,
)

_LOCAL = "http://unsloth:8000/v1"
_OPENAI = "https://api.openai.com/v1"
_OPENROUTER = "https://openrouter.ai/api/v1"

_UNSET = object()


@pytest.fixture(autouse=True)
def _clean_negotiation_cache():
    reset_negotiation_cache()
    yield
    reset_negotiation_cache()


def _completion(content="ok", finish_reason="stop", **message_extra) -> dict:
    message = {"role": "assistant", "content": content}
    message.update(message_extra)
    return {"choices": [{"message": message, "finish_reason": finish_reason}]}


def _ok(content="ja", finish_reason="stop", **message_extra) -> httpx.Response:
    return httpx.Response(200, json=_completion(content, finish_reason, **message_extra))


def _bad(
    message: str = "nope",
    *,
    param: object = _UNSET,
    code: str | None = "unsupported_parameter",
) -> httpx.Response:
    error: dict = {"message": message, "type": "invalid_request_error"}
    if param is not _UNSET:
        error["param"] = param
    if code is not None:
        error["code"] = code
    return httpx.Response(400, json={"error": error})


class _Recorder:
    """MockTransport handler that replays canned responses and records bodies."""

    def __init__(self, responses):
        self.bodies: list[dict] = []
        self._responses = list(responses)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        if self._responses:
            return self._responses.pop(0)
        return httpx.Response(500, json={"error": {"message": "no canned response"}})


def _client(recorder: _Recorder, *, base_url=_LOCAL, model="m", api_key="k") -> OpenAICompatClient:
    c = OpenAICompatClient(base_url=base_url, model=model, api_key=api_key)
    c._client = httpx.AsyncClient(base_url=c._base_url, transport=httpx.MockTransport(recorder))
    return c


# --- a. drop temperature --------------------------------------------------


@pytest.mark.asyncio
async def test_drop_temperature():
    rec = _Recorder([_bad("Unsupported parameter: 'temperature'", param="temperature"), _ok()])
    c = _client(rec)
    assert await c.generate("p", num_predict=120) == "ja"
    assert len(rec.bodies) == 2
    first = dict(rec.bodies[0])
    first.pop("temperature")
    assert rec.bodies[1] == first


# --- b/c. drop by message only --------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("quoted", ["'temperature'", '"temperature"', "`temperature`"])
async def test_drop_by_message_only(quoted):
    rec = _Recorder([_bad(f"Unsupported parameter: {quoted} is not supported with this model."), _ok()])
    c = _client(rec)
    assert await c.generate("p") == "ja"
    assert len(rec.bodies) == 2
    assert "temperature" not in rec.bodies[1]


@pytest.mark.asyncio
async def test_null_param_uses_message():
    rec = _Recorder([_bad("Unsupported parameter: 'temperature' is not supported.", param=None), _ok()])
    c = _client(rec)
    assert await c.generate("p") == "ja"
    assert len(rec.bodies) == 2
    assert "temperature" not in rec.bodies[1]


# --- d. a non-negotiable param blocks the message fallback ----------------


@pytest.mark.asyncio
async def test_param_messages_no_message_fallback():
    rec = _Recorder([_bad("Unsupported parameter: 'temperature' is not supported.", param="messages"), _ok()])
    c = _client(rec)
    with pytest.raises(LLMError, match="400"):
        await c.generate("p")
    assert len(rec.bodies) == 1


# --- e. cap-field swap ----------------------------------------------------


@pytest.mark.asyncio
async def test_swap_max_completion_tokens_for_max_tokens():
    rec = _Recorder(
        [_bad("Unsupported parameter: 'max_completion_tokens'", param="max_completion_tokens"), _ok()]
    )
    c = _client(rec, base_url=_OPENAI)
    assert await c.generate("p", num_predict=120) == "ja"
    assert len(rec.bodies) == 2
    assert rec.bodies[1]["max_tokens"] == 120
    assert "max_completion_tokens" not in rec.bodies[1]


@pytest.mark.asyncio
async def test_swap_max_tokens_for_max_completion_tokens():
    rec = _Recorder([_bad("Unsupported parameter: 'max_tokens'", param="max_tokens"), _ok()])
    c = _client(rec)
    assert await c.generate("p", num_predict=120) == "ja"
    assert len(rec.bodies) == 2
    assert rec.bodies[1]["max_completion_tokens"] == 120
    assert "max_tokens" not in rec.bodies[1]


# --- f. never ping-pong the cap field -------------------------------------


@pytest.mark.asyncio
async def test_no_cap_field_ping_pong():
    rec = _Recorder(
        [
            _bad("Unsupported parameter: 'max_tokens'", param="max_tokens"),
            _bad("Unsupported parameter: 'max_completion_tokens'", param="max_completion_tokens"),
            _ok(),
        ]
    )
    c = _client(rec)
    with pytest.raises(LLMError, match="400"):
        await c.generate("p")
    assert len(rec.bodies) == 2


# --- g. unsupported value on a cap field fails ----------------------------


@pytest.mark.asyncio
async def test_cap_field_value_error_fails():
    rec = _Recorder(
        [
            _bad(
                "Unsupported value: 'max_tokens' must be positive",
                param="max_tokens",
                code="unsupported_value",
            ),
            _ok(),
        ]
    )
    c = _client(rec)
    with pytest.raises(LLMError, match="400"):
        await c.generate("p")
    assert len(rec.bodies) == 1


# --- h. message naming both cap fields picks the one we sent --------------


@pytest.mark.asyncio
async def test_message_naming_both_cap_fields_uses_sent_one():
    rec = _Recorder(
        [
            _bad(
                "Unsupported parameter: 'max_tokens' is not supported with this model. "
                "Use 'max_completion_tokens' instead.",
                code=None,
            ),
            _ok(),
        ]
    )
    c = _client(rec)
    assert await c.generate("p", num_predict=120) == "ja"
    assert len(rec.bodies) == 2
    assert rec.bodies[1]["max_completion_tokens"] == 120
    assert "max_tokens" not in rec.bodies[1]


# --- i/j. reasoning_effort value ladder -----------------------------------


@pytest.mark.asyncio
async def test_reasoning_effort_value_ladder():
    rec = _Recorder(
        [
            _bad(
                "Unsupported value: 'reasoning_effort' does not support 'none'",
                param="reasoning_effort",
                code="unsupported_value",
            ),
            _ok(),
        ]
    )
    c = _client(rec, base_url=_OPENAI)
    assert await c.generate("p") == "ja"
    assert len(rec.bodies) == 2
    assert rec.bodies[0]["reasoning_effort"] == "none"
    assert rec.bodies[1]["reasoning_effort"] == "minimal"
    assert _SHAPES[(_OPENAI, "m", "openai")] == {"reasoning_effort": "minimal"}


@pytest.mark.asyncio
async def test_reasoning_effort_ladder_to_the_end():
    rec = _Recorder(
        [
            _bad("Unsupported value: 'none'", param="reasoning_effort", code="unsupported_value"),
            _bad("Unsupported value: 'minimal'", param="reasoning_effort", code="unsupported_value"),
            _bad("Unsupported value: 'low'", param="reasoning_effort", code="unsupported_value"),
            _ok(),
        ]
    )
    c = _client(rec, base_url=_OPENAI)
    assert await c.generate("p") == "ja"
    assert len(rec.bodies) == 4
    assert [b.get("reasoning_effort") for b in rec.bodies] == ["none", "minimal", "low", None]
    assert _SHAPES[(_OPENAI, "m", "openai")] == {"drop": ["reasoning_effort"]}


@pytest.mark.asyncio
async def test_reasoning_effort_ladder_by_message():
    rec = _Recorder(
        [
            _bad(
                "Unsupported value: 'reasoning_effort' does not support 'none' with this model.",
                code=None,
            ),
            _ok(),
        ]
    )
    c = _client(rec, base_url=_OPENAI)
    assert await c.generate("p") == "ja"
    assert rec.bodies[1]["reasoning_effort"] == "minimal"


# --- k. reasoning_effort dropped, not laddered ----------------------------


@pytest.mark.asyncio
async def test_reasoning_effort_unsupported_parameter_dropped():
    rec = _Recorder([_bad("Unsupported parameter: 'reasoning_effort'", param="reasoning_effort"), _ok()])
    c = _client(rec, base_url=_OPENAI)
    assert await c.generate("p") == "ja"
    assert len(rec.bodies) == 2
    assert "reasoning_effort" not in rec.bodies[1]


@pytest.mark.asyncio
async def test_reasoning_effort_value_error_local_dropped():
    rec = _Recorder(
        [
            _bad(
                "Unsupported value: 'reasoning_effort' does not support 'none'",
                param="reasoning_effort",
                code="unsupported_value",
            ),
            _ok(),
        ]
    )
    c = _client(rec)
    assert await c.generate("p") == "ja"
    assert len(rec.bodies) == 2
    assert "reasoning_effort" not in rec.bodies[1]


# --- l. drop reasoning / chat_template_kwargs -----------------------------


@pytest.mark.asyncio
async def test_drop_reasoning_dotted_param():
    rec = _Recorder([_bad("Unsupported parameter: 'reasoning'", param="reasoning.effort"), _ok()])
    c = _client(rec, base_url=_OPENROUTER)
    assert await c.generate("p") == "ja"
    assert len(rec.bodies) == 2
    assert "reasoning" not in rec.bodies[1]


@pytest.mark.asyncio
async def test_bare_word_inside_longer_name_does_not_match():
    rec = _Recorder([_bad("Unsupported parameter: 'reasoning_effort' is not supported."), _ok()])
    c = _client(rec, base_url=_OPENROUTER)
    with pytest.raises(LLMError, match="400"):
        await c.generate("p")
    assert len(rec.bodies) == 1


@pytest.mark.asyncio
async def test_drop_chat_template_kwargs():
    rec = _Recorder(
        [_bad("Unsupported parameter: 'chat_template_kwargs'", param="chat_template_kwargs"), _ok()]
    )
    c = _client(rec)
    assert await c.generate("p") == "ja"
    assert len(rec.bodies) == 2
    assert "chat_template_kwargs" not in rec.bodies[1]


# --- m. response_format drop builds the schema hint -----------------------


@pytest.mark.asyncio
async def test_drop_response_format_appends_schema_hint():
    schema = {"type": "object", "properties": {"iso_code": {"type": "string"}}}
    messages = [{"role": "user", "content": "guess"}]
    rec = _Recorder(
        [
            _bad("Unsupported parameter: 'response_format'", param="response_format"),
            _ok(),
            _ok(),
        ]
    )
    c = _client(rec)
    result = await c._chat(messages, cap=120, temperature=0.0, format_schema=schema, schema_name="subarr")
    assert result == "ja"
    assert "response_format" not in rec.bodies[1]
    suffix = "\n\nReply with only a JSON object matching this JSON schema: " + json.dumps(
        schema, sort_keys=True
    )
    assert rec.bodies[1]["messages"][-1]["content"].endswith(suffix)
    assert rec.bodies[0]["messages"] == messages
    assert messages == [{"role": "user", "content": "guess"}]

    assert await c._chat(messages, cap=120, temperature=0.0, format_schema=None, schema_name="subarr")
    assert rec.bodies[2]["messages"] == messages
    assert "response_format" not in rec.bodies[2]


# --- n. negotiation retry budget ------------------------------------------


@pytest.mark.asyncio
async def test_negotiation_retry_cap():
    schema = {"type": "object"}
    rec = _Recorder(
        [
            _bad("Unsupported parameter: 'temperature'", param="temperature"),
            _bad("Unsupported parameter: 'reasoning_effort'", param="reasoning_effort"),
            _bad("Unsupported parameter: 'chat_template_kwargs'", param="chat_template_kwargs"),
            _bad("Unsupported parameter: 'response_format'", param="response_format"),
        ]
    )
    c = _client(rec)
    with pytest.raises(LLMError, match="400"):
        await c.generate("p", format_schema=schema)
    assert len(rec.bodies) == 4


# --- o. other 400s --------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_400_fails_once():
    rec = _Recorder([_bad("something went wrong", param=None), _ok()])
    c = _client(rec)
    with pytest.raises(LLMError, match="400"):
        await c.generate("p")
    assert len(rec.bodies) == 1


@pytest.mark.asyncio
async def test_non_json_400_names_quoted_parameter():
    rec = _Recorder([httpx.Response(400, text="bad parameter 'temperature' here"), _ok()])
    c = _client(rec)
    assert await c.generate("p") == "ja"
    assert len(rec.bodies) == 2
    assert "temperature" not in rec.bodies[1]


@pytest.mark.asyncio
async def test_400_naming_absent_parameter_fails_once():
    rec = _Recorder([_bad("Unsupported parameter: 'response_format'", param="response_format"), _ok()])
    c = _client(rec)
    with pytest.raises(LLMError, match="400"):
        await c.generate("p")
    assert len(rec.bodies) == 1


# --- p. a dropped parameter cannot be named again -------------------------


@pytest.mark.asyncio
async def test_already_dropped_parameter_fails():
    rec = _Recorder(
        [
            _bad("Unsupported parameter: 'temperature'", param="temperature"),
            _bad("Unsupported parameter: 'temperature'", param="temperature"),
            _ok(),
        ]
    )
    c = _client(rec)
    with pytest.raises(LLMError, match="400"):
        await c.generate("p")
    assert len(rec.bodies) == 2


# --- q. learned shape reuse -----------------------------------------------


@pytest.mark.asyncio
async def test_learned_shape_reused():
    rec = _Recorder([_bad("Unsupported parameter: 'temperature'", param="temperature"), _ok(), _ok()])
    c = _client(rec)
    await c.generate("p")
    assert len(rec.bodies) == 2

    assert await c.generate("p") == "ja"
    assert len(rec.bodies) == 3
    assert "temperature" not in rec.bodies[2]


@pytest.mark.asyncio
async def test_learned_shape_shared_with_new_client():
    rec = _Recorder([_bad("Unsupported parameter: 'temperature'", param="temperature"), _ok()])
    c = _client(rec)
    await c.generate("p")

    rec2 = _Recorder([_ok()])
    c2 = _client(rec2, base_url=_LOCAL, model="m")
    assert await c2.generate("p") == "ja"
    assert len(rec2.bodies) == 1
    assert "temperature" not in rec2.bodies[0]


@pytest.mark.asyncio
async def test_learned_shape_not_shared_with_other_model():
    rec = _Recorder([_bad("Unsupported parameter: 'temperature'", param="temperature"), _ok()])
    c = _client(rec)
    await c.generate("p")

    rec2 = _Recorder([_ok()])
    c2 = _client(rec2, base_url=_LOCAL, model="other")
    assert await c2.generate("p") == "ja"
    assert rec2.bodies[0]["temperature"] == 0.0


@pytest.mark.asyncio
async def test_use_learned_false_starts_from_defaults_and_forgets():
    rec = _Recorder([_bad("Unsupported parameter: 'temperature'", param="temperature"), _ok(), _ok()])
    c = _client(rec)
    await c.generate("p")
    key = (_LOCAL, "m", "local")
    assert key in _SHAPES

    assert await c.generate("p", use_learned=False) == "ja"
    assert rec.bodies[2]["temperature"] == 0.0
    assert key not in _SHAPES


@pytest.mark.asyncio
async def test_use_learned_false_overwrites_learned_entry():
    rec = _Recorder(
        [
            _bad("Unsupported parameter: 'temperature'", param="temperature"),
            _ok(),
            _bad("Unsupported parameter: 'chat_template_kwargs'", param="chat_template_kwargs"),
            _ok(),
        ]
    )
    c = _client(rec)
    await c.generate("p")
    key = (_LOCAL, "m", "local")
    assert _SHAPES[key] == {"drop": ["temperature"]}

    assert await c.generate("p", use_learned=False) == "ja"
    assert "temperature" in rec.bodies[2]
    assert _SHAPES[key] == {"drop": ["chat_template_kwargs"]}


# --- r. reset_negotiation_cache -------------------------------------------


def test_reset_negotiation_cache_clears_in_place():
    _SHAPES[("http://x/v1", "m", "local")] = {"drop": ["temperature"]}
    before = id(_SHAPES)
    reset_negotiation_cache()
    assert _SHAPES == {}
    assert id(_SHAPES) == before


# --- s. write-back happens before reply parsing ---------------------------


@pytest.mark.asyncio
async def test_write_back_on_empty_reply():
    rec = _Recorder(
        [_bad("Unsupported parameter: 'temperature'", param="temperature"), _ok("", finish_reason="stop")]
    )
    c = _client(rec)
    with pytest.raises(LLMError, match="empty reply"):
        await c.generate("p")
    assert _SHAPES[(_LOCAL, "m", "local")] == {"drop": ["temperature"]}


# --- t/z. reasoning retry raises the cap ----------------------------------


@pytest.mark.asyncio
async def test_reasoning_retry_raises_cap():
    rec = _Recorder([_ok("", finish_reason="length", reasoning="thinking..."), _ok("ja")])
    c = _client(rec)
    assert await c.generate("p", num_predict=120) == "ja"
    assert len(rec.bodies) == 2
    assert rec.bodies[0]["max_tokens"] == 120
    assert rec.bodies[1]["max_tokens"] == 1024
    assert _SHAPES[(_LOCAL, "m", "local")] == {"raise_cap": True}

    rec2 = _Recorder([_ok("ja")])
    c2 = _client(rec2, base_url=_LOCAL, model="m")
    assert await c2.generate("p", num_predict=120) == "ja"
    assert len(rec2.bodies) == 1
    assert rec2.bodies[0]["max_tokens"] == 1024

    rec3 = _Recorder([_ok("ja")])
    c3 = _client(rec3, base_url=_LOCAL, model="m")
    assert await c3.generate("p", num_predict=220) == "ja"
    assert rec3.bodies[0]["max_tokens"] == 1760


@pytest.mark.asyncio
@pytest.mark.parametrize("cap, raised", [(256, 2048), (300, 2048), (4096, 4096)])
async def test_raised_cap_values(cap, raised):
    rec = _Recorder([_ok("", finish_reason="length", reasoning="thinking..."), _ok("ja")])
    c = _client(rec)
    assert await c.generate("p", num_predict=cap) == "ja"
    assert rec.bodies[1]["max_tokens"] == raised


# --- u. other reasoning fields --------------------------------------------


@pytest.mark.asyncio
async def test_reasoning_content_counts_as_reasoning():
    rec = _Recorder([_ok("", finish_reason="length", reasoning_content="thinking..."), _ok("ja")])
    c = _client(rec)
    assert await c.generate("p", num_predict=120) == "ja"
    assert len(rec.bodies) == 2
    assert rec.bodies[1]["max_tokens"] == 1024


@pytest.mark.asyncio
async def test_unclosed_think_counts_as_reasoning():
    rec = _Recorder([_ok("<think>still going", finish_reason="length"), _ok("ja")])
    c = _client(rec)
    assert await c.generate("p", num_predict=120) == "ja"
    assert len(rec.bodies) == 2
    assert rec.bodies[1]["max_tokens"] == 1024


# --- v/w/x. no retry in the other cases -----------------------------------


@pytest.mark.asyncio
async def test_reasoning_retry_once_only():
    rec = _Recorder(
        [
            _ok("", finish_reason="length", reasoning="thinking..."),
            _ok("", finish_reason="length", reasoning="still thinking..."),
            _ok("ja"),
        ]
    )
    c = _client(rec)
    with pytest.raises(LLMError, match="reply budget"):
        await c.generate("p", num_predict=120)
    assert len(rec.bodies) == 2


@pytest.mark.asyncio
async def test_no_retry_without_reasoning():
    rec = _Recorder([_ok("", finish_reason="length"), _ok("ja")])
    c = _client(rec)
    with pytest.raises(LLMError, match="reply budget"):
        await c.generate("p")
    assert len(rec.bodies) == 1


@pytest.mark.asyncio
async def test_empty_stop_with_reasoning_is_empty_reply():
    rec = _Recorder([_ok("", finish_reason="stop", reasoning="thinking..."), _ok("ja")])
    c = _client(rec)
    with pytest.raises(LLMError, match="empty reply"):
        await c.generate("p")
    assert len(rec.bodies) == 1


@pytest.mark.asyncio
async def test_cap_already_raised_does_not_retry():
    _SHAPES[(_LOCAL, "m", "local")] = {"raise_cap": True}
    rec = _Recorder([_ok("", finish_reason="length", reasoning="thinking..."), _ok("ja")])
    c = _client(rec)
    with pytest.raises(LLMError, match="reply budget"):
        await c.generate("p", num_predict=120)
    assert len(rec.bodies) == 1
    assert rec.bodies[0]["max_tokens"] == 1024


# --- y. the reasoning retry has its own budget ----------------------------


@pytest.mark.asyncio
async def test_reasoning_retry_outside_negotiation_budget():
    rec = _Recorder(
        [
            _bad("Unsupported parameter: 'temperature'", param="temperature"),
            _bad("Unsupported parameter: 'reasoning_effort'", param="reasoning_effort"),
            _bad("Unsupported parameter: 'chat_template_kwargs'", param="chat_template_kwargs"),
            _ok("", finish_reason="length", reasoning="thinking..."),
            _ok("ja"),
        ]
    )
    c = _client(rec)
    assert await c.generate("p") == "ja"
    assert len(rec.bodies) == 5


# --- aa. non-400 errors are unchanged -------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 401, 404])
async def test_non_400_errors_fail_once(status):
    body = {
        "error": {
            "message": "Unsupported parameter: 'temperature'",
            "type": "invalid_request_error",
            "param": "temperature",
            "code": "unsupported_parameter",
        }
    }
    rec = _Recorder([httpx.Response(status, json=body), _ok()])
    c = _client(rec)
    with pytest.raises(LLMError):
        await c.generate("p")
    assert len(rec.bodies) == 1


# --- bb. redaction still holds --------------------------------------------


_KEY = "sk-SECRET-123456"


@pytest.mark.asyncio
async def test_key_redacted_when_negotiation_fails():
    rec = _Recorder([httpx.Response(400, text=f"bad params with key {_KEY}")])
    c = _client(rec, api_key=_KEY)
    with pytest.raises(LLMError) as excinfo:
        await c.generate("p")
    assert _KEY not in str(excinfo.value)


@pytest.mark.asyncio
async def test_key_never_logged_during_negotiation(caplog):
    caplog.set_level(logging.DEBUG)
    rec = _Recorder([httpx.Response(400, text=f"Unsupported parameter: 'temperature' ({_KEY})"), _ok()])
    c = _client(rec, api_key=_KEY)
    assert await c.generate("p") == "ja"
    for record in caplog.records:
        assert _KEY not in record.getMessage()


# --- cc. empty shape body unchanged ---------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url,expected",
    [
        (
            _LOCAL,
            {
                "model",
                "messages",
                "temperature",
                "max_tokens",
                "reasoning_effort",
                "chat_template_kwargs",
            },
        ),
        (
            _OPENAI,
            {
                "model",
                "messages",
                "temperature",
                "max_completion_tokens",
                "reasoning_effort",
            },
        ),
        (_OPENROUTER, {"model", "messages", "temperature", "max_tokens", "reasoning"}),
    ],
)
async def test_empty_shape_body_unchanged(base_url, expected):
    rec = _Recorder([_ok()])
    c = _client(rec, base_url=base_url)
    await c.generate("p", num_predict=120)
    assert set(rec.bodies[0]) == expected
