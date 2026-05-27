"""Ollama HTTP client.

Subarr uses ollama to enrich coverage rows where Sonarr's `originalLanguage`
is null/und (unknown). We send the title + folder context and ask for an
ISO 639-1 code. Cheap, single-shot — generated text is short, no streaming
needed.

GPU-idle gating happens at the caller level (the enrichment endpoint
checks /api/queue idle first). This client is just the transport.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from ..config import settings

log = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    pass


# Ollama can take a while on first model load. Generous read timeout.
_OLLAMA_TIMEOUT = httpx.Timeout(connect=3.0, read=120.0, write=10.0, pool=3.0)


class OllamaClient:
    def __init__(self, base_url: str | None = None, model: str | None = None,
                 timeout: httpx.Timeout | None = None):
        self._base_url = (base_url or settings.ollama_url).rstrip("/")
        self._model = model or settings.ollama_model
        self._configured = bool(self._base_url and self._model)
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout or _OLLAMA_TIMEOUT)

    def is_configured(self) -> bool:
        return self._configured

    @property
    def model(self) -> str:
        return self._model

    async def aclose(self) -> None:
        await self._client.aclose()

    async def tags(self) -> dict[str, Any]:
        """GET /api/tags — list of installed models. Used for health probe."""
        try:
            r = await self._client.get("/api/tags")
        except httpx.HTTPError as e:
            raise OllamaError(f"ollama /api/tags failed: {e}") from e
        if r.status_code != 200:
            raise OllamaError(f"ollama /api/tags status {r.status_code}")
        return r.json()

    async def generate(self, prompt: str, *, system: str | None = None,
                        temperature: float = 0.0, num_predict: int = 64) -> str:
        """POST /api/generate with stream=false. Returns the generated text.
        Low temperature for deterministic ISO-code outputs."""
        if not self._configured:
            raise OllamaError("ollama not configured (set OLLAMA_URL + OLLAMA_MODEL)")
        body: dict[str, Any] = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": num_predict},
        }
        if system:
            body["system"] = system
        try:
            r = await self._client.post("/api/generate", json=body)
        except httpx.HTTPError as e:
            raise OllamaError(f"ollama /api/generate failed: {e}") from e
        if r.status_code != 200:
            raise OllamaError(f"ollama /api/generate status {r.status_code}: {r.text[:200]}")
        try:
            data = r.json()
        except ValueError as e:
            raise OllamaError(f"ollama returned non-json: {e}") from e
        return (data.get("response") or "").strip()
