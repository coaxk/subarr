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
                        temperature: float = 0.0, num_predict: int = 64,
                        keep_alive: str | int | None = None,
                        format_schema: dict | None = None) -> str:
        """POST /api/generate with stream=false. Returns the generated text.

        Low temperature for deterministic ISO-code outputs.

        v1.1-D: `keep_alive` controls how long Ollama keeps the model
        resident in VRAM after the call. Defaults to Ollama's 5-min behavior.
        Pass "30m" during coverage walks to avoid re-warming on every row,
        and `0` (or "0s") after the batch to free VRAM for subgen.

        v1.1-C: `format_schema` enables structured JSON outputs against a
        JSON Schema. When set, Ollama enforces the shape — replaces our
        brittle string-parse paths."""
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
        if keep_alive is not None:
            body["keep_alive"] = keep_alive
        if format_schema is not None:
            body["format"] = format_schema
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

    async def vision_describe(
        self, *, image_url: str | None = None, image_b64: str | None = None,
        prompt: str, model: str | None = None, num_predict: int = 256,
    ) -> str:
        """v1.1-K: vision-capable Ollama call. Pass either a URL we fetch
        and base64-encode, or pre-encoded base64. Uses qwen2.5vl by default
        (configurable via the `model` arg — e.g. for llama3.2-vision).

        Used by subarr's vision pre-filter to ask the model:
          'Does this frame show hardcoded/burned-in subtitles?'
          'Is this a no-dialog scene (just action/music)?'
        Skip subgen when the answer is yes for either."""
        import base64, httpx
        if not self._configured:
            raise OllamaError("ollama not configured")
        if image_url and not image_b64:
            try:
                async with httpx.AsyncClient(timeout=30.0) as fetch:
                    r = await fetch.get(image_url)
                    r.raise_for_status()
                    image_b64 = base64.b64encode(r.content).decode("ascii")
            except Exception as e:
                raise OllamaError(f"image fetch failed: {e}") from e
        if not image_b64:
            raise OllamaError("vision_describe needs image_url or image_b64")
        body: dict[str, Any] = {
            "model": model or self._model,
            "prompt": prompt,
            "images": [image_b64],
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": num_predict},
        }
        try:
            r = await self._client.post("/api/generate", json=body)
        except httpx.HTTPError as e:
            raise OllamaError(f"ollama vision: {e}") from e
        if r.status_code != 200:
            raise OllamaError(f"ollama vision HTTP {r.status_code}: {r.text[:200]}")
        try:
            data = r.json()
        except ValueError as e:
            raise OllamaError(f"ollama vision non-json: {e}") from e
        return (data.get("response") or "").strip()

    async def unload(self) -> None:
        """v1.1-D: Force-unload the current model from VRAM. Implemented
        as a no-op generate() with keep_alive=0 — Ollama interprets that
        as 'evict immediately after this call'.

        Used by the Settings → Unload Model button (frees VRAM for subgen)
        and by the coverage walk's epilogue to release the model after
        enrichment finishes."""
        if not self._configured:
            return
        try:
            await self.generate("", num_predict=1, keep_alive=0)
        except OllamaError:
            pass
