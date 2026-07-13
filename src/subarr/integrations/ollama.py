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
from .base import _is_client_closed

log = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    pass


# Ollama can take a while on first model load. Generous read timeout.
_OLLAMA_TIMEOUT = httpx.Timeout(connect=3.0, read=120.0, write=10.0, pool=3.0)


# #232: Known vision-capable Ollama model families. Used to detect
# whether the user has ANY vision model installed when they have not
# explicitly configured OLLAMA_VISION_MODEL (or set it to "auto").
# Match is on the family prefix because users tag size/quantisation
# variants (qwen2.5vl:7b-q4, llava:13b-v1.6, etc.) — anything starting
# with one of these is a fair candidate. Order = preference: qwen2.5vl
# is current best-in-class for our specific job (thumb classification).
_VISION_FAMILIES = (
    "qwen2.5vl",  # current default + recommended
    "qwen2-vl",  # predecessor, still common
    "llama3.2-vision",
    "llava",
    "bakllava",
    "minicpm-v",
    "moondream",
)


def _is_vision_capable(model_name: str) -> bool:
    """Family-prefix match against the known-vision-capable allowlist."""
    if not model_name:
        return False
    n = model_name.lower()
    return any(n.startswith(f) for f in _VISION_FAMILIES)


def _match_configured_model(installed: list[str], cfg: str) -> str | None:
    """Return the installed model matching an EXPLICIT OLLAMA_VISION_MODEL,
    tolerant of the optional :tag suffix: a bare `qwen2.5vl` matches installed
    `qwen2.5vl:7b`, while a tagged `qwen2.5vl:7b` matches only that exact tag.
    The user opting in beats the vision allowlist — a valid vision model we have
    not hardcoded still resolves. None if the configured model is not installed."""
    if not cfg:
        return None
    cfg = cfg.strip().lower()
    for name in installed:
        if name.lower() == cfg:
            return name  # exact (tagged) match
    if ":" not in cfg:  # config gave no tag -> match any tag of that base name
        for name in installed:
            if name.lower().split(":", 1)[0] == cfg:
                return name
    return None


class OllamaClient:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        vision_model: str | None = None,
        timeout: httpx.Timeout | None = None,
    ):
        self._base_url = (base_url or settings.ollama_url).rstrip("/")
        self._model = model or settings.ollama_model
        # #232: explicit vision-model knob. "auto" defers picking until
        # the first capability probe — we pick the first installed model
        # whose name matches the vision allowlist.
        self._vision_model_config = vision_model or settings.ollama_vision_model
        self._vision_model_resolved: str | None = None  # cached after probe
        self._configured = bool(self._base_url and self._model)
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout or _OLLAMA_TIMEOUT)

    def is_configured(self) -> bool:
        return self._configured

    @property
    def model(self) -> str:
        return self._model

    @property
    def vision_model_config(self) -> str:
        """The configured vision-model identifier (could be 'auto')."""
        return self._vision_model_config

    async def aclose(self) -> None:
        await self._client.aclose()

    async def version(self) -> str | None:
        """GET /api/version — Ollama's own version string for the Settings
        status grid. Best-effort: returns None on any failure (the tags()
        probe owns reachability; version is cosmetic)."""
        try:
            r = await self._client.get("/api/version")
            if r.status_code == 200:
                v = (r.json() or {}).get("version")
                return str(v) if v else None
        except (httpx.HTTPError, ValueError):
            pass
        except RuntimeError as e:  # #392: closed-client mid-rebuild → best-effort None
            if not _is_client_closed(e):
                raise
        return None

    async def tags(self) -> dict[str, Any]:
        """GET /api/tags — list of installed models. Used for health probe."""
        try:
            r = await self._client.get("/api/tags")
        except httpx.HTTPError as e:
            raise OllamaError(f"ollama /api/tags failed: {e}") from e
        except RuntimeError as e:  # #392: client closed mid-rebuild → graceful
            if not _is_client_closed(e):
                raise
            raise OllamaError("ollama /api/tags: client closed mid-request (live rebuild)") from e
        if r.status_code != 200:
            raise OllamaError(f"ollama /api/tags status {r.status_code}")
        return r.json()

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.0,
        num_predict: int = 64,
        keep_alive: str | int | None = None,
        format_schema: dict | None = None,
    ) -> str:
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
        except RuntimeError as e:  # #392: client closed mid-rebuild → graceful
            if not _is_client_closed(e):
                raise
            raise OllamaError("ollama /api/generate: client closed mid-request (live rebuild)") from e
        if r.status_code != 200:
            raise OllamaError(f"ollama /api/generate status {r.status_code}: {r.text[:200]}")
        try:
            data = r.json()
        except ValueError as e:
            raise OllamaError(f"ollama returned non-json: {e}") from e
        return (data.get("response") or "").strip()

    async def installed_models(self) -> list[str]:
        """#232: list installed model tags. Thin wrapper over /api/tags
        that returns just the name strings — used by the vision-model
        resolver and by the onboarding wizard's model picker."""
        try:
            data = await self.tags()
        except OllamaError:
            return []
        models = data.get("models", []) if isinstance(data, dict) else []
        return [m.get("name", "") for m in models if isinstance(m, dict)]

    async def resolve_vision_model(self) -> str | None:
        """#232: figure out which vision-capable model to use right now.

        Logic:
          1. If OLLAMA_VISION_MODEL is set to an explicit model name AND
             that model is installed → use it as-is (user opted in).
          2. If set to "auto" OR set explicitly but the model is NOT
             installed → walk /api/tags and pick the first installed
             model whose name family matches the vision allowlist.
          3. If nothing vision-capable is installed → return None.
             Callers MUST treat None as "vision pre-filter unavailable"
             and gracefully skip — never call vision_describe in that
             state, the text model would hallucinate.

        Result is cached on the instance until reset_vision_cache() is
        called (Settings save / model pull completion clears it)."""
        if self._vision_model_resolved is not None:
            return self._vision_model_resolved or None
        if not self._configured:
            self._vision_model_resolved = ""
            return None
        installed = await self.installed_models()
        if not installed:
            self._vision_model_resolved = ""
            return None
        cfg = self._vision_model_config.strip().lower()
        # Path 1: explicit model that is installed. Tag-tolerant, and the user's
        # explicit choice beats the allowlist — a valid vision model we have not
        # hardcoded (a newer/custom one) still resolves. This is the #232 fix for
        # "no vision-capable models installed" when the user DID configure one.
        if cfg and cfg != "auto":
            match = _match_configured_model(installed, cfg)
            if match:
                self._vision_model_resolved = match
                return match
        # Path 2: auto-pick first vision-capable installed model
        # Prefer in the order of _VISION_FAMILIES (best-fit first)
        for family in _VISION_FAMILIES:
            for name in installed:
                if name.lower().startswith(family):
                    self._vision_model_resolved = name
                    return name
        # Path 3: nothing vision-capable installed
        self._vision_model_resolved = ""
        return None

    def reset_vision_cache(self) -> None:
        """Clear the resolved-vision-model cache so the next call to
        resolve_vision_model() re-probes /api/tags. Settings save +
        model-pull completion both invoke this."""
        self._vision_model_resolved = None

    async def pull_model(self, name: str):
        """POST /api/pull stream — pulls a model image. Yields raw JSON
        progress events as bytes lines so the frontend can render a
        progress bar without re-decoding. Used by the onboarding wizard
        and Settings "Pull qwen2.5vl" button."""
        if not self._configured:
            raise OllamaError("ollama not configured")
        body = {"name": name, "stream": True}
        try:
            async with self._client.stream("POST", "/api/pull", json=body) as r:
                if r.status_code != 200:
                    text = (await r.aread()).decode("utf-8", "replace")[:200]
                    raise OllamaError(f"ollama /api/pull HTTP {r.status_code}: {text}")
                async for chunk in r.aiter_lines():
                    if chunk.strip():
                        yield chunk
        except httpx.HTTPError as e:
            raise OllamaError(f"ollama /api/pull failed: {e}") from e
        except RuntimeError as e:  # #392: client closed mid-rebuild → graceful
            if not _is_client_closed(e):
                raise
            raise OllamaError("ollama /api/pull: client closed mid-request (live rebuild)") from e
        # New model — invalidate the vision-resolve cache.
        self.reset_vision_cache()

    async def vision_describe(
        self,
        *,
        image_url: str | None = None,
        image_b64: str | None = None,
        prompt: str,
        model: str | None = None,
        num_predict: int = 256,
    ) -> str:
        """v1.1-K: vision-capable Ollama call. Pass either a URL we fetch
        and base64-encode, or pre-encoded base64.

        #232 hardened: model selection now goes through
        resolve_vision_model() instead of falling back to self._model
        (which is the TEXT model in most installs). If no vision-capable
        model is installed, this raises OllamaError("no vision model")
        and the caller MUST surface that as "vision pre-filter
        unavailable" rather than treating it as a runtime failure —
        every other ollama feature still works with the text model."""
        import base64
        import httpx

        if not self._configured:
            raise OllamaError("ollama not configured")
        # #232: pick the vision model BEFORE fetching the image, so we
        # fail fast and don't waste a Tautulli round-trip.
        resolved = model or await self.resolve_vision_model()
        if not resolved:
            raise OllamaError(
                "no vision-capable model installed (configure OLLAMA_VISION_MODEL "
                "or pull a model from: " + ", ".join(_VISION_FAMILIES[:3]) + ")"
            )
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
            "model": resolved,
            "prompt": prompt,
            "images": [image_b64],
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": num_predict},
        }
        try:
            r = await self._client.post("/api/generate", json=body)
        except httpx.HTTPError as e:
            raise OllamaError(f"ollama vision: {e}") from e
        except RuntimeError as e:  # #392: client closed mid-rebuild → graceful
            if not _is_client_closed(e):
                raise
            raise OllamaError("ollama vision: client closed mid-request (live rebuild)") from e
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
