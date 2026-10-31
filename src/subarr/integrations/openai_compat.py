"""OpenAI-compatible HTTP client (#616).

Speaks the `/chat/completions` dialect of llama-server, Unsloth, Ollama's
/v1 shim, OpenAI and OpenRouter. Same job as OllamaClient — short,
single-shot generations at temperature 0 — but the wire format and the
thinking-off knobs differ per server profile (see `llm.resolve_profile`).
"""

from __future__ import annotations

from typing import Any

import httpx

from ..config import settings
from .base import _is_client_closed
from .llm import LLMError, resolve_profile

# #616: a rejected parameter (Task 3) and rate limits (Task 4) are handled
# later; for now every non-200 fails the call immediately.

# Local servers can take a while on first model load. Hosted endpoints get a
# slightly longer connect budget (TLS + DNS + a shared edge).
_LOCAL_TIMEOUT = httpx.Timeout(connect=3.0, read=120.0, write=10.0, pool=3.0)
_HOSTED_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=3.0)


def _redact(text: str, key: str) -> str:
    """Replace every occurrence of the API key with `***`.

    The empty-key guard matters: str.replace("", ...) would splice the marker
    between every character."""
    if not key:
        return text
    return text.replace(key, "***")


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
        )

    async def _chat(
        self,
        messages: list[dict[str, str]],
        *,
        cap: int,
        temperature: float,
        format_schema: dict | None,
        schema_name: str,
    ) -> str:
        """Build the profile's request body, send it and unwrap the reply.

        Task 3 grows the body/retry logic here; keep this the single place a
        completion request is made."""
        if not self._configured:
            raise LLMError("llm not configured (set OPENAI_BASE_URL + OPENAI_MODEL)")
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
        }
        # Token-cap field and thinking-off knobs differ per profile. Ollama
        # only honours reasoning_effort, llama-server/Unsloth only
        # chat_template_kwargs, so the local profile sends both.
        if self.profile == "openai":
            body["max_completion_tokens"] = cap
            body["reasoning_effort"] = "none"
        elif self.profile == "openrouter":
            body["max_tokens"] = cap
            body["reasoning"] = {"effort": "none"}
        else:
            body["max_tokens"] = cap
            body["reasoning_effort"] = "none"
            body["chat_template_kwargs"] = {"enable_thinking": False}
        if format_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": format_schema, "strict": False},
            }
        try:
            r = await self._client.post("/chat/completions", json=body, headers=self._headers())
        except httpx.HTTPError as e:
            raise LLMError(_redact(f"llm /chat/completions failed: {e}", self._api_key)) from e
        except RuntimeError as e:  # #392: client closed mid-rebuild → graceful
            if not _is_client_closed(e):
                raise
            raise LLMError("llm /chat/completions: client closed mid-request (live rebuild)") from e
        if r.status_code in (401, 403):
            # Never include the body: a proxy may echo the key back.
            raise LLMError("LLM API key rejected (check OPENAI_API_KEY)")
        if r.status_code == 404:
            raise LLMError("model not found at this server (check OPENAI_MODEL and the /v1 base URL)")
        if r.status_code != 200:
            # Redact the whole body FIRST, then truncate: a key straddling the
            # 200-char cut must not leave a partial key behind.
            safe = _redact(r.text, self._api_key)[:200]
            raise LLMError(f"llm /chat/completions status {r.status_code}: {safe}")
        try:
            data = r.json()
        except ValueError as e:
            raise LLMError(_redact(f"llm returned non-json: {e}", self._api_key)) from e
        choices = data.get("choices") if isinstance(data, dict) else None
        if not choices:
            raise LLMError("llm reply has no choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise LLMError("llm reply has no message")
        content = message.get("content")
        text = ("" if content is None else str(content)).strip()
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
                raise LLMError(
                    "the model used its whole reply budget reasoning; "
                    "turn thinking off on the server or choose a non-thinking model"
                )
            raise LLMError("the model returned an empty reply")
        return text

    async def unload(self) -> None:
        """No-op: the OpenAI dialect has no model-residency knob (no keep_alive)."""
        return
