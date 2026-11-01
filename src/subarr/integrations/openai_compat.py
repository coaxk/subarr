"""OpenAI-compatible HTTP client (#616).

Speaks the `/chat/completions` dialect of llama-server, Unsloth, Ollama's
/v1 shim, OpenAI and OpenRouter. Same job as OllamaClient — short,
single-shot generations at temperature 0 — but the wire format and the
thinking-off knobs differ per server profile (see `llm.resolve_profile`).
"""

from __future__ import annotations

import asyncio
import base64
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

# #616: image fetches use their own client, so tests can swap the transport.
_IMAGE_FETCH_TIMEOUT = 30.0
_image_fetch_transport: httpx.AsyncBaseTransport | None = None

# Learned request shapes keyed by (base_url, model, profile).
_SHAPES: dict[tuple[str, str, str], dict] = {}


def reset_negotiation_cache() -> None:
    """Forget every learned request shape."""
    _SHAPES.clear()


def _image_mime(image_b64: str) -> str:
    """Best-effort MIME type from the magic bytes at the head of the payload."""
    try:
        head = base64.b64decode(image_b64[:24], validate=False)
    except ValueError:
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


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
    provider = "openai"
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
        self._last_reply_had_reasoning: bool | None = None

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    @property
    def last_reply_had_reasoning(self) -> bool | None:
        """Whether the last parsed 200 reply carried reasoning, else None.

        Test connection reads it on a client it owns; on a shared client
        concurrent calls overwrite it."""
        return self._last_reply_had_reasoning

    @property
    def is_local_gpu(self) -> bool:
        return self.profile == "local"

    def is_configured(self) -> bool:
        return self._configured

    @property
    def model(self) -> str:
        return self._model

    @property
    def vision_model_config(self) -> str:
        """The raw OPENAI_VISION setting (e.g. "off", "same" or a model id)."""
        return self._vision

    @property
    def vision_model(self) -> str | None:
        """The vision model id, or None when vision is off."""
        v = self._vision
        if v == "" or v.lower() == "off":
            return None
        if v.lower() == "same":
            return self._model or None
        return v

    async def resolve_vision_model(self, installed: list[str] | None = None) -> str | None:
        """#616: the vision model id; `installed` is accepted for signature
        compatibility with OllamaClient.resolve_vision_model and ignored."""
        return self.vision_model

    def reset_vision_cache(self) -> None:
        """No-op: there is no cache; exists for signature compatibility with OllamaClient."""
        return

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            return {}
        return {"Authorization": f"Bearer {self._api_key}"}

    async def models(self) -> list[str]:
        """#616: GET /models — the server's model ids, in server order."""
        if not self._base_url:
            raise LLMError("llm not configured (set OPENAI_BASE_URL)")
        # #616: this is the liveness probe, so it must report the server's real
        # state, and a working /models must not reset the completion failure
        # count while completions fail; the breaker is neither gated nor recorded.
        try:
            r = await self._client.get("/models", headers=self._headers())
        except httpx.HTTPError as e:
            raise LLMError(_redact(f"llm /models failed: {e}", self._api_key)) from None
        except RuntimeError as e:  # #616: client closed mid-rebuild → graceful
            if not _is_client_closed(e):
                raise
            raise LLMError("llm /models: client closed mid-request (live rebuild)") from e
        if r.status_code in (401, 403):
            # Never include the body: a proxy may echo the key back.
            raise LLMError("LLM API key rejected (check OPENAI_API_KEY)", status=r.status_code)
        if r.status_code == 404:
            raise LLMError("no /v1/models here; the URL usually ends in /v1", status=404)
        if r.status_code != 200:
            # Redact the whole body FIRST, then truncate.
            safe = _redact(r.text, self._api_key)[:200]
            raise LLMError(f"llm /models status {r.status_code}: {safe}", status=r.status_code)
        try:
            data = r.json()
        except ValueError as e:
            raise LLMError(_redact(f"llm /models returned non-json: {e}", self._api_key)) from None
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise LLMError("llm /models reply has no data list")
        return [
            item["id"]
            for item in items
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
        ]

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

    async def vision_describe(
        self,
        *,
        image_url: str | None = None,
        image_b64: str | None = None,
        prompt: str,
        model: str | None = None,
        num_predict: int = 256,
    ) -> str:
        """#616: describe an image through /chat/completions.

        Pass either a URL we fetch and base64-encode, or pre-encoded base64
        (which wins when both are given)."""
        if not self._configured:
            raise LLMError("llm not configured (set OPENAI_BASE_URL + OPENAI_MODEL)")
        vision_model = self.vision_model
        if vision_model is None:
            raise LLMError("vision not configured (set OPENAI_VISION to same or a model id)")
        chosen = model.strip() if model and model.strip() else vision_model
        if not image_b64 and not image_url:
            raise LLMError("vision_describe needs image_url or image_b64")
        if not image_b64:
            assert image_url is not None
            image_b64 = await self._fetch_image_b64(image_url)
        data_uri = f"data:{_image_mime(image_b64)};base64,{image_b64}"
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ]
        return await self._chat(
            messages,
            cap=num_predict,
            temperature=0.1,
            format_schema=None,
            schema_name="subarr",
            model=chosen,
        )

    async def _fetch_image_b64(self, image_url: str) -> str:
        """Fetch an image with a throwaway client and return its base64 body."""
        try:
            # #616: routers/vision.py allowlists only the URL it was given, so a
            # redirect must not be followed.
            async with httpx.AsyncClient(
                timeout=_IMAGE_FETCH_TIMEOUT,
                transport=_image_fetch_transport,
                follow_redirects=False,
            ) as fetch:
                r = await fetch.get(image_url)
                content = r.content
        # The URL can carry a Plex token or a Tautulli API key in its query
        # string, and httpx exception text carries the URL, so only the type name.
        except Exception as e:
            raise LLMError(f"image fetch failed: {type(e).__name__}") from None
        if 300 <= r.status_code <= 399:
            raise LLMError(f"image fetch failed: HTTP {r.status_code} (redirects are not followed)")
        if r.status_code != 200:
            raise LLMError(f"image fetch failed: HTTP {r.status_code}")
        if not content:
            raise LLMError("image fetch returned no data")
        return base64.b64encode(content).decode("ascii")

    async def _chat(
        self,
        messages: list[dict[str, Any]],
        *,
        cap: int,
        temperature: float,
        format_schema: dict | None,
        schema_name: str,
        use_learned: bool = True,
        model: str | None = None,
    ) -> str:
        """Build the profile's request body, send it and unwrap the reply.

        Parameter negotiation, the reasoning retry and the retry-after wait
        happen in this loop; keep this the single place a completion request
        is made."""
        model_id = model or self._model
        if not self._configured:
            raise LLMError("llm not configured (set OPENAI_BASE_URL + OPENAI_MODEL)")
        # #616: fail fast while the per-client breaker is open.
        if not self._breaker.allow():
            raise LLMCoolingDown("LLM provider cooling down after repeated failures; try again in a minute")
        self._last_reply_had_reasoning = None
        key = (self._base_url, model_id, self.profile)
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
                    model_id=model_id,
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
                    # #616: a rejected key counts as a failure, so a revoked hosted key opens the breaker.
                    outcome = False
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
                            model_id,
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
                # A reply that used its whole budget and shows no content spent it
                # on reasoning the server did not return; a plain reply cut at the
                # cap still has content, so it is not counted.
                seen = (
                    _has_reasoning(message)
                    or raw.strip().startswith("<think>")
                    or (not text and finish_reason == "length")
                )
                self._last_reply_had_reasoning = seen or bool(self._last_reply_had_reasoning)
                if not text:
                    if finish_reason == "length":
                        reasoning_present = _has_reasoning(message) or raw.strip().startswith("<think>")
                        if reasoning_present and not raised_now:
                            shape["raise_cap"] = True
                            log.info(
                                "#616: %s reply was all reasoning; retrying with a %s cap (model %s)",
                                self.profile,
                                _raised(cap),
                                model_id,
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
        messages: list[dict[str, Any]],
        *,
        cap: int,
        temperature: float,
        format_schema: dict | None,
        schema_name: str,
        model_id: str,
    ) -> dict[str, Any]:
        drop = set(shape.get("drop", ()))
        body: dict[str, Any] = {"model": model_id, "messages": messages}
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
                body["model"],
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
                    body["model"],
                )
            else:
                new_value = "minimal" if current == "none" else "low"
                shape["reasoning_effort"] = new_value
                log.info(
                    "#616: %s rejected %s; retrying with %s (model %s)",
                    self.profile,
                    named,
                    new_value,
                    body["model"],
                )
            return True, swapped_cap
        _drop(shape, named)
        log.info(
            "#616: %s rejected %s; retrying without it (model %s)",
            self.profile,
            named,
            body["model"],
        )
        return True, swapped_cap

    async def unload(self) -> None:
        """No-op: the OpenAI dialect has no model-residency knob (no keep_alive)."""
        return
