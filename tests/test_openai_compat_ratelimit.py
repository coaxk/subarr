"""#616: the retry-after wait and the per-client circuit breaker.

The transport is mocked with httpx.MockTransport driven by a list of canned
responses (or handlers that raise), like tests/test_openai_compat_client.py.
`_sleep` is patched in every test, so nothing really waits.
"""

from __future__ import annotations

import asyncio
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
    reset_negotiation_cache,
)

_LOCAL = "http://unsloth:8000/v1"
_MODEL = "m"
_KEY = "sk-SECRET-123456"


@pytest.fixture(autouse=True)
def _clean_negotiation_cache():
    reset_negotiation_cache()
    yield
    reset_negotiation_cache()


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


def _counting_breaker(now) -> tuple[CircuitBreaker, list[int]]:
    breaker = _breaker(now)
    allow_calls: list[int] = []
    real_allow = breaker.allow

    def allow():
        allow_calls.append(1)
        return real_allow()

    breaker.allow = allow
    return breaker, allow_calls


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


def _unsupported_temperature() -> httpx.Response:
    return httpx.Response(
        400,
        json={"error": {"message": "Unsupported parameter: 'temperature'", "param": "temperature"}},
    )


def _raises(exc: Exception):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


class _Recorder:
    """MockTransport handler replaying canned responses and recording requests."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.bodies: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        self.bodies.append(json.loads(request.content))
        item = self.responses.pop(0)
        return item(request) if callable(item) else item


def _client(recorder, *, breaker=None, api_key="k", base_url=_LOCAL, model=_MODEL):
    kwargs = {}
    if breaker is not None:
        kwargs["breaker"] = breaker
    c = OpenAICompatClient(base_url=base_url, model=model, api_key=api_key, **kwargs)
    c._client = httpx.AsyncClient(base_url=c._base_url, transport=httpx.MockTransport(recorder))
    return c


# --- a. a 429 wait then a 200 ---------------------------------------------


@pytest.mark.asyncio
async def test_429_retry_after_then_success(slept):
    rec = _Recorder([_err(429, retry_after="2"), _ok()])
    c = _client(rec)
    assert await c.generate("p") == "ja"
    assert slept == [2.0]
    assert rec.calls == 2


# --- b. retry-after above the cap is not honoured -------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["31", "30.5"])
async def test_429_retry_after_above_cap_not_honoured(value, slept):
    rec = _Recorder([_err(429, retry_after=value), _ok()])
    c = _client(rec)
    with pytest.raises(LLMError, match="429"):
        await c.generate("p")
    assert slept == []
    assert rec.calls == 1


# --- c. the honoured range ------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("value,expected", [("30", 30.0), ("0", 0.0), ("1.5", 1.5)])
async def test_429_retry_after_honoured_values(value, expected, slept):
    rec = _Recorder([_err(429, retry_after=value), _ok()])
    c = _client(rec)
    assert await c.generate("p") == "ja"
    assert slept == [expected]
    assert rec.calls == 2


# --- d. a 429 without a retry-after header --------------------------------


@pytest.mark.asyncio
async def test_429_without_retry_after_fails(slept):
    rec = _Recorder([_err(429), _ok()])
    c = _client(rec)
    with pytest.raises(LLMError, match="429"):
        await c.generate("p")
    assert slept == []
    assert rec.calls == 1


# --- e. values that are not honoured --------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    ["Wed, 21 Oct 2026 07:28:00 GMT", "-1", "nan", "inf", "", "soon"],
)
async def test_429_retry_after_not_honoured_values(value, slept):
    rec = _Recorder([_err(429, retry_after=value), _ok()])
    c = _client(rec)
    with pytest.raises(LLMError, match="429"):
        await c.generate("p")
    assert slept == []
    assert rec.calls == 1


# --- f. every 5xx is retried, not just 503 --------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status,value", [(503, "2"), (500, "1")])
async def test_5xx_retry_after_then_success(status, value, slept):
    rec = _Recorder([_err(status, retry_after=value), _ok()])
    c = _client(rec)
    assert await c.generate("p") == "ja"
    assert slept == [float(value)]
    assert rec.calls == 2


# --- g. at most one wait per call -----------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("second", [429, 503])
async def test_only_one_retry_after_wait_per_call(second, slept):
    rec = _Recorder([_err(429, retry_after="1"), _err(second, retry_after="1"), _ok()])
    c = _client(rec)
    with pytest.raises(LLMError, match=str(second)):
        await c.generate("p")
    assert slept == [1.0]
    assert rec.calls == 2


# --- h. retry-after is ignored on other statuses --------------------------


@pytest.mark.asyncio
async def test_retry_after_ignored_on_400(slept):
    rec = _Recorder([_err(400, retry_after="1"), _ok()])
    c = _client(rec)
    with pytest.raises(LLMError, match="400"):
        await c.generate("p")
    assert slept == []
    assert rec.calls == 1


@pytest.mark.asyncio
async def test_retry_after_ignored_on_401(slept):
    rec = _Recorder([_err(401, retry_after="1"), _ok()])
    c = _client(rec)
    with pytest.raises(LLMError, match="API key rejected"):
        await c.generate("p")
    assert slept == []
    assert rec.calls == 1


# --- i. a wait is not part of the negotiation budget ----------------------


@pytest.mark.asyncio
async def test_wait_then_negotiation(slept):
    rec = _Recorder([_err(429, retry_after="1"), _unsupported_temperature(), _ok()])
    c = _client(rec)
    assert await c.generate("p") == "ja"
    assert rec.calls == 3
    assert "temperature" not in rec.bodies[2]
    assert slept == [1.0]


# --- j. the breaker opens after five failures -----------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "item",
    [
        pytest.param(lambda: _err(500), id="500"),
        pytest.param(lambda: _raises(httpx.ConnectError("boom")), id="connect-error"),
        pytest.param(lambda: _err(429), id="429"),
    ],
)
async def test_breaker_opens_after_five_failures(item, now):
    rec = _Recorder([item() for _ in range(5)])
    breaker = _breaker(now)
    c = _client(rec, breaker=breaker)
    for _ in range(5):
        with pytest.raises(LLMError) as excinfo:
            await c.generate("p")
        assert not isinstance(excinfo.value, LLMCoolingDown)
    with pytest.raises(LLMCoolingDown, match="cooling down"):
        await c.generate("p")
    assert rec.calls == 5
    assert breaker.state == "open"


# --- k. a wait that still fails counts one failure ------------------------


@pytest.mark.asyncio
async def test_waited_and_failed_counts_one_failure(now, slept):
    rec = _Recorder([_err(429, retry_after="1"), _err(429, retry_after="1")] * 5)
    breaker = _breaker(now)
    c = _client(rec, breaker=breaker)
    for _ in range(5):
        with pytest.raises(LLMError) as excinfo:
            await c.generate("p")
        assert not isinstance(excinfo.value, LLMCoolingDown)
    with pytest.raises(LLMCoolingDown):
        await c.generate("p")
    assert rec.calls == 10
    assert slept == [1.0] * 5


# --- l. a wait that succeeds counts one success ---------------------------


@pytest.mark.asyncio
async def test_waited_success_resets_failures(now, slept):
    rec = _Recorder([_err(500)] * 4 + [_err(429, retry_after="1"), _ok()] + [_err(500)] * 4 + [_ok()])
    breaker = _breaker(now)
    c = _client(rec, breaker=breaker)
    for _ in range(4):
        with pytest.raises(LLMError):
            await c.generate("p")
    assert await c.generate("p") == "ja"
    for _ in range(4):
        with pytest.raises(LLMError):
            await c.generate("p")
    assert breaker.state == "closed"
    assert await c.generate("p") == "ja"
    assert rec.calls == 11
    assert slept == [1.0]


# --- m. client errors are not provider failures ---------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "item",
    [
        pytest.param(lambda: _err(400), id="400"),
        pytest.param(lambda: _err(404), id="404"),
    ],
)
async def test_client_errors_do_not_trip_breaker(item, now):
    rec = _Recorder([item() for _ in range(6)])
    breaker = _breaker(now)
    c = _client(rec, breaker=breaker)
    for _ in range(6):
        with pytest.raises(LLMError):
            await c.generate("p")
    assert rec.calls == 6
    assert breaker.state == "closed"


# --- n. a reachable reply resets the count --------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reachable",
    [_err(404), _ok("")],
    ids=["404", "empty-200"],
)
async def test_reachable_reply_resets_failure_count(reachable, now):
    rec = _Recorder([_err(500)] * 4 + [reachable] + [_err(500)] * 4 + [_ok()])
    breaker = _breaker(now)
    c = _client(rec, breaker=breaker)
    for _ in range(4):
        with pytest.raises(LLMError):
            await c.generate("p")
    with pytest.raises(LLMError):
        await c.generate("p")
    for _ in range(4):
        with pytest.raises(LLMError):
            await c.generate("p")
    assert breaker.state == "closed"
    assert await c.generate("p") == "ja"


# --- n2. a rejected key opens the breaker ---------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403], ids=["401", "403"])
async def test_rejected_key_opens_breaker(status, now):
    rec = _Recorder([_err(status) for _ in range(5)])
    breaker = _breaker(now)
    c = _client(rec, breaker=breaker)
    for _ in range(5):
        with pytest.raises(LLMError, match="API key rejected") as excinfo:
            await c.generate("p")
        assert not isinstance(excinfo.value, LLMCoolingDown)
    with pytest.raises(LLMCoolingDown):
        await c.generate("p")
    assert rec.calls == 5


@pytest.mark.asyncio
async def test_rejected_key_counts_with_server_errors(now):
    rec = _Recorder([_err(500)] * 4 + [_err(401)])
    breaker = _breaker(now)
    c = _client(rec, breaker=breaker)
    for _ in range(4):
        with pytest.raises(LLMError):
            await c.generate("p")
    with pytest.raises(LLMError, match="API key rejected"):
        await c.generate("p")
    assert breaker.state == "open"
    with pytest.raises(LLMCoolingDown):
        await c.generate("p")
    assert rec.calls == 5


@pytest.mark.asyncio
async def test_rejected_key_is_not_reset_by_404(now):
    rec = _Recorder([_err(500)] * 4 + [_err(404)] + [_err(401)] * 5)
    breaker = _breaker(now)
    c = _client(rec, breaker=breaker)
    for _ in range(4):
        with pytest.raises(LLMError):
            await c.generate("p")
    with pytest.raises(LLMError, match="model not found"):
        await c.generate("p")
    for _ in range(4):
        with pytest.raises(LLMError, match="API key rejected"):
            await c.generate("p")
    assert breaker.state == "closed"
    with pytest.raises(LLMError, match="API key rejected"):
        await c.generate("p")
    assert breaker.state == "open"


@pytest.mark.asyncio
async def test_rejected_key_on_half_open_probe_reopens(now):
    rec = _Recorder([_err(500)] * 5 + [_err(401)])
    breaker = _breaker(now)
    c = _client(rec, breaker=breaker)
    for _ in range(5):
        with pytest.raises(LLMError):
            await c.generate("p")
    assert breaker.state == "open"
    now[0] = 60.0
    with pytest.raises(LLMError, match="API key rejected"):
        await c.generate("p")
    with pytest.raises(LLMCoolingDown):
        await c.generate("p")
    assert rec.calls == 6


@pytest.mark.asyncio
async def test_rejected_key_never_waits(now, slept):
    rec = _Recorder([_err(401, retry_after="1")])
    breaker = _breaker(now)
    for _ in range(4):
        breaker.record_failure()
    c = _client(rec, breaker=breaker)
    with pytest.raises(LLMError, match="API key rejected"):
        await c.generate("p")
    assert rec.calls == 1
    assert slept == []
    assert breaker.state == "open"


@pytest.mark.asyncio
async def test_models_rejected_key_stays_outside_breaker(now):
    breaker = _breaker(now)
    for _ in range(4):
        breaker.record_failure()
    c = OpenAICompatClient(base_url=_LOCAL, model=_MODEL, api_key="k", breaker=breaker)
    c._client = httpx.AsyncClient(
        base_url=c._base_url,
        transport=httpx.MockTransport(lambda request: httpx.Response(401, text="no")),
    )
    with pytest.raises(LLMError, match="API key rejected"):
        await c.models()
    assert breaker.state == "closed"


@pytest.mark.asyncio
async def test_rejected_key_message_is_fixed():
    rec = _Recorder([_err(401, text=f"invalid key {_KEY}")])
    c = _client(rec, api_key=_KEY)
    with pytest.raises(LLMError) as excinfo:
        await c.generate("p")
    assert str(excinfo.value) == "LLM API key rejected (check OPENAI_API_KEY)"


@pytest.mark.asyncio
async def test_rejected_key_after_retry_after_wait_counts_once(now, slept):
    rec = _Recorder([_err(429, retry_after="1"), _err(401)])
    breaker = _breaker(now)
    for _ in range(3):
        breaker.record_failure()
    c = _client(rec, breaker=breaker)
    with pytest.raises(LLMError, match="API key rejected"):
        await c.generate("p")
    assert rec.calls == 2
    assert slept == [1.0]
    assert breaker.state == "closed"
    breaker.record_failure()
    assert breaker.state == "open"


# --- o. a negotiated success counts once ----------------------------------


@pytest.mark.asyncio
async def test_negotiation_success_counts_once(now):
    rec = _Recorder([_err(500)] * 4 + [_unsupported_temperature(), _ok()] + [_err(500)] * 4)
    breaker = _breaker(now)
    c = _client(rec, breaker=breaker)
    for _ in range(4):
        with pytest.raises(LLMError):
            await c.generate("p")
    assert await c.generate("p") == "ja"
    for _ in range(4):
        with pytest.raises(LLMError):
            await c.generate("p")
    assert breaker.state == "closed"
    assert rec.calls == 10


# --- p. cooldown recovery -------------------------------------------------


@pytest.mark.asyncio
async def test_cooldown_recovery(now):
    rec = _Recorder([_ok()])
    breaker = _breaker(now)
    c = _client(rec, breaker=breaker)
    for _ in range(5):
        breaker.record_failure()
    now[0] = 59.9
    with pytest.raises(LLMCoolingDown):
        await c.generate("p")
    assert rec.calls == 0
    now[0] = 60.0
    assert await c.generate("p") == "ja"
    assert rec.calls == 1
    assert breaker.state == "closed"


# --- q. a failed probe re-opens the breaker -------------------------------


@pytest.mark.asyncio
async def test_failed_probe_reopens_cooldown(now):
    rec = _Recorder([_err(500), _ok()])
    breaker = _breaker(now)
    c = _client(rec, breaker=breaker)
    for _ in range(5):
        breaker.record_failure()
    now[0] = 60.0
    with pytest.raises(LLMError):
        await c.generate("p")
    assert rec.calls == 1
    with pytest.raises(LLMCoolingDown):
        await c.generate("p")
    assert rec.calls == 1
    now[0] = 120.0
    assert await c.generate("p") == "ja"
    assert rec.calls == 2


# --- r. the default breaker -----------------------------------------------


@pytest.mark.asyncio
async def test_default_breaker():
    rec = _Recorder([_err(500)] * 5)
    c = _client(rec)
    assert c.breaker._cooldown_s == 60.0
    assert c.breaker._fail_threshold == 5
    for _ in range(5):
        with pytest.raises(LLMError):
            await c.generate("p")
    with pytest.raises(LLMCoolingDown):
        await c.generate("p")
    assert rec.calls == 5


# --- s. every client gets its own breaker ---------------------------------


@pytest.mark.asyncio
async def test_per_client_breaker_isolation():
    rec_a = _Recorder([_err(500)] * 5)
    a = _client(rec_a)
    for _ in range(5):
        with pytest.raises(LLMError):
            await a.generate("p")

    rec_b = _Recorder([_ok()])
    b = _client(rec_b)
    assert await b.generate("p") == "ja"
    assert rec_b.calls == 1

    assert a.breaker is not b.breaker
    assert a.breaker is not _client(_Recorder([])).breaker


# --- t. an unconfigured client never touches the breaker ------------------


@pytest.mark.asyncio
async def test_unconfigured_client_never_touches_breaker(now):
    breaker, allow_calls = _counting_breaker(now)
    rec = _Recorder([])
    c = _client(rec, breaker=breaker, base_url="", model=_MODEL)
    for _ in range(10):
        with pytest.raises(LLMError, match="not configured"):
            await c.generate("p")
    assert allow_calls == []
    assert breaker.state == "closed"
    assert rec.calls == 0


# --- u. the closed-client path records no outcome -------------------------


@pytest.mark.asyncio
async def test_closed_client_records_no_outcome(now):
    closed = _raises(RuntimeError("Cannot send a request, as the client has been closed."))
    rec = _Recorder([closed, _err(500)])
    breaker = _breaker(now)
    for _ in range(4):
        breaker.record_failure()
    c = _client(rec, breaker=breaker)
    with pytest.raises(LLMError, match="client closed"):
        await c.generate("p")
    assert breaker.state == "closed"
    with pytest.raises(LLMError):
        await c.generate("p")
    assert breaker.state == "open"
    with pytest.raises(LLMCoolingDown):
        await c.generate("p")
    assert rec.calls == 2


@pytest.mark.asyncio
async def test_unrelated_runtime_error_records_no_outcome(now):
    rec = _Recorder([_raises(RuntimeError("other")), _err(500)])
    breaker = _breaker(now)
    for _ in range(4):
        breaker.record_failure()
    c = _client(rec, breaker=breaker)
    with pytest.raises(RuntimeError) as excinfo:
        await c.generate("p")
    assert not isinstance(excinfo.value, LLMError)
    assert breaker.state == "closed"
    with pytest.raises(LLMError):
        await c.generate("p")
    assert breaker.state == "open"


# --- v. a cancelled wait records no outcome -------------------------------


@pytest.mark.asyncio
async def test_cancelled_sleep_records_no_outcome(monkeypatch, now):
    async def cancelled(delay: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(openai_compat, "_sleep", cancelled)
    rec = _Recorder([_err(429, retry_after="1"), _err(500)])
    breaker = _breaker(now)
    for _ in range(4):
        breaker.record_failure()
    c = _client(rec, breaker=breaker)
    with pytest.raises(asyncio.CancelledError):
        await c.generate("p")
    assert rec.calls == 1
    assert breaker.state == "closed"
    with pytest.raises(LLMError):
        await c.generate("p")
    assert breaker.state == "open"


# --- w. the gate is checked once per call ---------------------------------


@pytest.mark.asyncio
async def test_allow_called_once_per_call(now, slept):
    rec = _Recorder([_unsupported_temperature(), _err(429, retry_after="1"), _ok()])
    breaker, allow_calls = _counting_breaker(now)
    c = _client(rec, breaker=breaker)
    assert await c.generate("p") == "ja"
    assert allow_calls == [1]
    assert slept == [1.0]
    assert rec.calls == 3


# --- x/y. exception chains carry no cause ---------------------------------


@pytest.mark.asyncio
async def test_transport_error_has_no_cause():
    rec = _Recorder([_raises(httpx.ConnectError(f"boom {_KEY}"))])
    c = _client(rec, api_key=_KEY)
    with pytest.raises(LLMError) as excinfo:
        await c.generate("p")
    exc = excinfo.value
    assert exc.__cause__ is None
    assert exc.__suppress_context__ is True
    assert _KEY not in "".join(traceback.format_exception(exc))
    assert _KEY not in str(exc)


@pytest.mark.asyncio
async def test_non_json_200_has_no_cause():
    rec = _Recorder([httpx.Response(200, text=f"not json {_KEY}")])
    c = _client(rec, api_key=_KEY)
    with pytest.raises(LLMError) as excinfo:
        await c.generate("p")
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True


# --- z. the wait log names no secrets -------------------------------------


@pytest.mark.asyncio
async def test_retry_log_is_redacted(caplog):
    caplog.set_level(logging.DEBUG)
    rec = _Recorder([_err(429, retry_after="1", text=f"limited {_KEY}"), _ok()])
    c = _client(rec, api_key=_KEY)
    assert await c.generate("p") == "ja"
    assert len([r for r in caplog.records if "waiting" in r.getMessage()]) == 1
    for record in caplog.records:
        assert _KEY not in record.getMessage()


# --- aa. cooling down is an LLMError --------------------------------------


@pytest.mark.asyncio
async def test_cooling_down_is_llm_error(now):
    rec = _Recorder([])
    breaker = _breaker(now)
    for _ in range(5):
        breaker.record_failure()
    c = _client(rec, breaker=breaker)
    with pytest.raises(LLMCoolingDown) as excinfo:
        await c.generate("p")
    assert isinstance(excinfo.value, LLMError)
    assert rec.calls == 0
