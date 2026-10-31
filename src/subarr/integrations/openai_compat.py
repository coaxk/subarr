"""OpenAI-compatible HTTP client (#616).

Speaks the `/chat/completions` dialect of llama-server, Unsloth, Ollama's
/v1 shim, OpenAI and OpenRouter. Same job as OllamaClient — short,
single-shot generations at temperature 0 — but the wire format and the
thinking-off knobs differ per server profile (see `llm.resolve_profile`).
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
from typing import Any

import httpx

from ..circuit_breaker import CircuitBreaker
from ..config import settings
from .base import _is_client_closed
from .llm import LLMError, resolve_profile

log = logging.getLogger(__name__)

# #616: a 400 that names one of the optional parameters is retried with that
# parameter dropped or changed; every other non-200 fails immediately, except
# a 429/5xx, which may be retried once after a `retry-after`.

# Local servers can take a while on first model load. Hosted endpoints get a
# slightly longer connect budget (TLS + DNS + a shared edge).
_LOCAL_TIMEOUT = httpx.Timeout(connect=3.0, read=120.0, write=10.0, pool=3.0)
_HOSTED_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=3.0)

# #616: the negotiable parameters, in message-match order.
_NEGOTIABLE = (
    "temperature",
    "reasoning_effort",
    "reasoning",
    "chat_template_kwargs",
    "response_format",
    "max_tokens",
    "max_completion_tokens",
)
_MAX_NEGOTIATIONS = 3

# #616: the longest `retry-after` wait honoured; longer values fail at once.
_RETRY_AFTER_MAX_S = 30.0

# Patchable sleep, so tests never wait.
_sleep = asyncio.sleep

# Learned request shapes keyed by (base_url, model, profile).
_SHAPES: dict[tuple[str, str, str], dict] = {}


def reset_negotiation_cache() -> None:
    """Forget every learned request shape."""
    _SHAPES.clear()


def _retry_after_delay(response: httpx.Response) -> float | None:
    """The `retry-after` delay in seconds, or None when it is not honoured."""
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        delay = float(value.strip())
    except ValueError:
        return None
    if not math.isfinite(delay) or not 0 <= delay <= _RETRY_AFTER_MAX_S:
        return None
    return delay


def _redact(text: str, key: str) -> str:
    """Replace every occurrence of the API key with `***`.

    The empty-key guard matters: str.replace("", ...) would splice the marker
    between every character."""
    if not key:
        return text
    return text.replace(key, "***")


def _raised(cap: int) -> int:
    return max(cap, min(max(cap * 8, 1024), 2048))


def _parse_error(text: str) -> tuple[str | None, str | None, str | None]:
    """Pull (param, message, code) out of a possibly OpenAI-style error body."""
    try:
        data = json.loads(text)
    except ValueError:
        return None, None, None
    if not isinstance(data, dict):
        return None, None, None
    error = data.get("error")
    if isinstance(error, dict):
        return (
            error.get("param") if isinstance(error.get("param"), str) else None,
            error.get("message") if isinstance(error.get("message"), str) else None,
            error.get("code") if isinstance(error.get("code"), str) else None,
        )
    if isinstance(error, str):
        return None, error, None
    return None, None, None


def _quoted(name: str, text: str) -> bool:
    return f"'{name}'" in text or f'"{name}"' in text or f"`{name}`" in text


def _has_reasoning(message: dict) -> bool:
    for field in ("reasoning", "reasoning_content"):
        value = message.get(field)
        if isinstance(value, str) and value.strip():
            return True
    return False


def _drop(shape: dict, name: str) -> None:
    shape.pop(name, None)
    shape["drop"] = sorted({*shape.get("drop", ()), name})


def _with_schema_hint(messages: list[dict[str, Any]], format_schema: dict) -> list[dict[str, Any]]:
    hint = "\n\nReply with only a JSON object matching this JSON schema: " + json.dumps(
        format_schema, sort_keys=True
    )
    copied = [dict(m) for m in messages]
    copied[-1]["content"] = (copied[-1].get("content") or "") + hint
    return copied


class LLMCoolingDown(LLMError):
    """#616: the client's breaker is open, so no request was sent."""


class OpenAICompatClient:
    # #616: capability flags the routers/UI branch on instead of isinstance.
    supports_pull = False
    supports_unload = False

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        vision: str | None = None,
        server_type: str | None = None,
        timeout: httpx.Timeout | None = None,
        breaker: CircuitBreaker | None = None,
    ):
        # `is None` (not `or`): an explicit "" means "no key", even when settings
        # holds one, so a caller can build a keyless client for a local server.
        raw_url = base_url if base_url is not None else settings.openai_base_url
        raw_key = api_key if api_key is not None else settings.openai_api_key
        raw_model = model if model is not None else settings.openai_model
        raw_vision = vision if vision is not None else settings.openai_vision
        self._base_url = raw_url.strip().rstrip("/")
        self._api_key = raw_key.strip()
        self._model = raw_model.strip()
        self._vision = raw_vision.strip()
        self.profile = resolve_profile(self._base_url, server_type)
        self._configured = bool(self._base_url and self._model)
        if timeout is None:
            timeout = _LOCAL_TIMEOUT if self.profile == "local" else _HOSTED_TIMEOUT
        # No Authorization header on the shared client: tests swap `_client`
        # for a MockTransport one, so the header is passed per request instead.
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout)
        # #616: per-client breaker, injected by the caller if tuned.
        self._breaker = breaker or CircuitBreaker(name="llm", fail_threshold=5, cooldown_s=60.0)

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    @property
    def is_local_gpu(self) -> bool:
        return self.profile == "local"

    def is_configured(self) -> bool:
        return self._configured

    @property
    def model(self) -> str:
        return self._model

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            return {}
        return {"Authorization": f"Bearer {self._api_key}"}

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.0,
        num_predict: int = 64,
        keep_alive: str | int | None = None,
        format_schema: dict | None = None,
        schema_name: str = "subarr",
        use_learned: bool = True,
    ) -> str:
        """POST /chat/completions. Returns the generated text.

        `keep_alive` exists only for signature compatibility with
        OllamaClient.generate — the OpenAI dialect has no equivalent, so it is
        never sent. `format_schema` becomes a `response_format` json_schema
        block (strict off: our schemas use minimum/maximum)."""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return await self._chat(
            messages,
            cap=num_predict,
            temperature=temperature,
            format_schema=format_schema,
            schema_name=schema_name,
            use_learned=use_learned,
        )

    async def _chat(
        self,
        messages: list[dict[str, str]],
        *,
        cap: int,
        temperature: float,
        format_schema: dict | None,
        schema_name: str,
        use_learned: bool = True,
    ) -> str:
        """Build the profile's request body, send it and unwrap the reply.

        Parameter negotiation, the reasoning retry and the retry-after wait
        happen in this loop; keep this the single place a completion request
        is made."""
        if not self._configured:
            raise LLMError("llm not configured (set OPENAI_BASE_URL + OPENAI_MODEL)")
        # #616: fail fast while the per-client breaker is open.
        if not self._breaker.allow():
            raise LLMCoolingDown("LLM provider cooling down after repeated failures; try again in a minute")
        key = (self._base_url, self._model, self.profile)
        shape = copy.deepcopy(_SHAPES.get(key, {})) if use_learned else {}
        negotiations = 0
        swapped_cap = False
        waited = False
        outcome: bool | None = None
        try:
            while True:
                raised_now = bool(shape.get("raise_cap"))
                body = self._build_body(
                    shape,
                    messages,
                    cap=cap,
                    temperature=temperature,
                    format_schema=format_schema,
                    schema_name=schema_name,
                )
                try:
                    r = await self._client.post("/chat/completions", json=body, headers=self._headers())
                except httpx.HTTPError as e:
                    outcome = False
                    raise LLMError(_redact(f"llm /chat/completions failed: {e}", self._api_key)) from None
                except RuntimeError as e:  # #392: client closed mid-rebuild → graceful
                    if not _is_client_closed(e):
                        raise
                    raise LLMError("llm /chat/completions: client closed mid-request (live rebuild)") from e
                if r.status_code in (401, 403):
                    # Never include the body: a proxy may echo the key back.
                    outcome = True
                    raise LLMError("LLM API key rejected (check OPENAI_API_KEY)")
                if r.status_code == 404:
                    outcome = True
                    raise LLMError("model not found at this server (check OPENAI_MODEL and the /v1 base URL)")
                if r.status_code == 400:
                    err_param, err_msg, err_code = _parse_error(r.text)
                    if err_msg is None:
                        err_msg = r.text
                    retry, swapped_cap = self._negotiate(
                        body, shape, err_param, err_msg, err_code, negotiations, swapped_cap
                    )
                    if retry:
                        negotiations += 1
                        continue
                if (r.status_code == 429 or r.status_code >= 500) and not waited:
                    delay = _retry_after_delay(r)
                    if delay is not None:
                        waited = True
                        log.info(
                            "#616: %s returned %s; waiting %ss before one retry (model %s)",
                            self.profile,
                            r.status_code,
                            delay,
                            self._model,
                        )
                        outcome = None
                        await _sleep(delay)
                        continue
                if r.status_code != 200:
                    outcome = not (r.status_code == 429 or r.status_code >= 500)
                    # Redact the whole body FIRST, then truncate: a key straddling the
                    # 200-char cut must not leave a partial key behind.
                    safe = _redact(r.text, self._api_key)[:200]
                    raise LLMError(f"llm /chat/completions status {r.status_code}: {safe}")
                outcome = True
                if shape:
                    _SHAPES[key] = copy.deepcopy(shape)
                else:
                    _SHAPES.pop(key, None)
                try:
                    data = r.json()
                except ValueError as e:
                    raise LLMError(_redact(f"llm returned non-json: {e}", self._api_key)) from None
                choices = data.get("choices") if isinstance(data, dict) else None
                if not choices:
                    raise LLMError("llm reply has no choices")
                message = choices[0].get("message") if isinstance(choices[0], dict) else None
                if not isinstance(message, dict):
                    raise LLMError("llm reply has no message")
                content = message.get("content")
                raw = "" if content is None else str(content)
                text = raw.strip()
                # Some servers inline reasoning in content; drop a LEADING block only.
                if text.startswith("<think>"):
                    end = text.find("</think>")
                    if end == -1:
                        text = ""  # all reasoning, no answer
                    else:
                        text = text[end + len("</think>") :].strip()
                finish_reason = choices[0].get("finish_reason")
                if not text:
                    if finish_reason == "length":
                        reasoning_present = _has_reasoning(message) or raw.strip().startswith("<think>")
                        if reasoning_present and not raised_now:
                            shape["raise_cap"] = True
                            log.info(
                                "#616: %s reply was all reasoning; retrying with a %s cap (model %s)",
                                self.profile,
                                _raised(cap),
                                self._model,
                            )
                            outcome = None
                            continue
                        raise LLMError(
                            "the model used its whole reply budget reasoning; "
                            "turn thinking off on the server or choose a non-thinking model"
                        )
                    raise LLMError("the model returned an empty reply")
                return text
        finally:
            if outcome is not None:
                if outcome:
                    self._breaker.record_success()
                else:
                    self._breaker.record_failure()

    def _cap_field(self) -> str:
        return "max_completion_tokens" if self.profile == "openai" else "max_tokens"

    def _build_body(
        self,
        shape: dict,
        messages: list[dict[str, str]],
        *,
        cap: int,
        temperature: float,
        format_schema: dict | None,
        schema_name: str,
    ) -> dict[str, Any]:
        drop = set(shape.get("drop", ()))
        body: dict[str, Any] = {"model": self._model, "messages": messages}
        if "temperature" not in drop:
            body["temperature"] = temperature
        # Token-cap field and thinking-off knobs differ per profile. Ollama
        # only honours reasoning_effort, llama-server/Unsloth only
        # chat_template_kwargs, so the local profile sends both.
        body[shape.get("cap_field", self._cap_field())] = _raised(cap) if shape.get("raise_cap") else cap
        if self.profile == "openai":
            if "reasoning_effort" not in drop:
                body["reasoning_effort"] = shape.get("reasoning_effort", "none")
        elif self.profile == "openrouter":
            if "reasoning" not in drop:
                body["reasoning"] = {"effort": "none"}
        else:
            if "reasoning_effort" not in drop:
                body["reasoning_effort"] = shape.get("reasoning_effort", "none")
            if "chat_template_kwargs" not in drop:
                body["chat_template_kwargs"] = {"enable_thinking": False}
        if format_schema is not None:
            if "response_format" in drop:
                body["messages"] = _with_schema_hint(messages, format_schema)
            else:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "schema": format_schema, "strict": False},
                }
        return body

    def _negotiate(
        self,
        body: dict[str, Any],
        shape: dict,
        err_param: str | None,
        err_msg: str,
        err_code: str | None,
        negotiations: int,
        swapped_cap: bool,
    ) -> tuple[bool, bool]:
        """Work out the retry for a 400; returns (retry, swapped_cap)."""
        named: str | None = None
        if err_param:
            candidate = err_param.split(".", 1)[0]
            if candidate in _NEGOTIABLE and candidate in body:
                named = candidate
        else:
            for name in _NEGOTIABLE:
                if name in body and _quoted(name, err_msg):
                    named = name
                    break
        if named is None or negotiations >= _MAX_NEGOTIATIONS:
            return False, swapped_cap
        value_error = err_code == "unsupported_value" or "unsupported value" in err_msg.lower()
        if named in ("max_tokens", "max_completion_tokens"):
            if value_error or swapped_cap:
                return False, swapped_cap
            other = "max_tokens" if named == "max_completion_tokens" else "max_completion_tokens"
            if other == self._cap_field():
                shape.pop("cap_field", None)
            else:
                shape["cap_field"] = other
            log.info(
                "#616: %s rejected %s; retrying with %s (model %s)",
                self.profile,
                named,
                other,
                self._model,
            )
            return True, True
        if named == "reasoning_effort" and value_error and self.profile == "openai":
            current = shape.get("reasoning_effort", "none")
            if current == "low":
                _drop(shape, named)
                log.info(
                    "#616: %s rejected %s; retrying without it (model %s)",
                    self.profile,
                    named,
                    self._model,
                )
            else:
                new_value = "minimal" if current == "none" else "low"
                shape["reasoning_effort"] = new_value
                log.info(
                    "#616: %s rejected %s; retrying with %s (model %s)",
                    self.profile,
                    named,
                    new_value,
                    self._model,
                )
            return True, swapped_cap
        _drop(shape, named)
        log.info(
            "#616: %s rejected %s; retrying without it (model %s)",
            self.profile,
            named,
            self._model,
        )
        return True, swapped_cap

    async def unload(self) -> None:
        """No-op: the OpenAI dialect has no model-residency knob (no keep_alive)."""
        return
