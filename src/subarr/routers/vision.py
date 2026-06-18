"""v1.1-K: Vision pre-filter endpoints.

POST /api/vision/check  — analyze a Plex episode thumbnail with a
                          vision-capable Ollama model and return:
                            { has_hardsubs: bool, dialog_density: 0..1,
                              skip_recommended: bool, reasoning: str }

Used to skip Whisper transcription on:
  - hardcoded/burned-in subtitle content (Whisper would produce dupes)
  - no-dialog scenes / clip shows / B-roll (no usable transcript anyway)

For v1.1 this is a manual endpoint. v1.2 wires it into the scan-submit
flow automatically, running on each candidate file's Plex thumbnail before
queueing subgen.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..config import settings
from ..error_detail import safe_error
from ..integrations.ollama import OllamaError, _VISION_FAMILIES

router = APIRouter(prefix="/api/vision", tags=["vision"])
log = logging.getLogger(__name__)


VISION_MODEL_DEFAULT = "qwen2.5vl:7b"

PROMPT = """Analyse this frame from a TV episode or movie.

Reply with valid JSON only, no preamble:
{
  "has_hardsubs": <true if visible burned-in subtitles or text overlay covering >5% of frame, else false>,
  "dialog_density": <0.0 to 1.0 estimate of how dialog-heavy this scene appears (0=action/music/silence, 1=talking heads)>,
  "scene_type": "<one word: dialog | action | montage | titles | credits | establishing | other>",
  "reasoning": "<one short sentence>"
}"""


class VisionCheckRequest(BaseModel):
    image_url: str | None = None
    image_b64: str | None = None
    model: str | None = None
    prompt: str | None = None  # override prompt for advanced callers


def _image_url_allowed(url: str, allowed_hosts: set[str]) -> bool:
    """True iff url is http(s) and its host[:port] is in allowed_hosts.

    image_url is fetched server-side by vision_describe, so an unrestricted
    value is an SSRF vector. Thumbnails only ever come from the configured
    Plex / Tautulli hosts, so we allowlist exactly those netlocs. An empty
    allowlist (neither configured) denies — the caller must pass image_b64.
    """
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme not in ("http", "https") or not p.netloc:
        return False
    return p.netloc.lower() in allowed_hosts


def _allowed_image_hosts() -> set[str]:
    """Netlocs (host[:port]) of the configured thumbnail sources."""
    hosts: set[str] = set()
    for url in (settings.plex_url, settings.tautulli_url):
        if url:
            netloc = urlparse(url).netloc.lower()
            if netloc:
                hosts.add(netloc)
    return hosts


class VisionPullRequest(BaseModel):
    # Onboarding + Settings "Pull vision model" button.
    # Default is qwen2.5vl:7b (current best-fit for thumb classification).
    name: str = VISION_MODEL_DEFAULT


@router.get("/status")
async def vision_status(request: Request) -> dict[str, Any]:
    """#232: per-product vision-capability status.

    Returns:
      {
        ollama_configured: bool,
        vision_model_config: str,        # what env says (e.g. "qwen2.5vl:7b" / "auto")
        vision_model_resolved: str|None, # what we actually use, after probing /api/tags
        vision_capable: bool,            # True iff resolved != None
        installed_models: list[str],     # everything on the box, for the picker
        vision_capable_installed: list[str],  # subset matching the allowlist
        suggested_pull: str,             # default to offer if vision_capable=False
        families_supported: list[str],   # the allowlist, for UI hints
      }
    """
    ollama = getattr(request.app.state, "ollama", None)
    if ollama is None or not ollama.is_configured():
        return {
            "ollama_configured": False,
            "vision_model_config": "",
            "vision_model_resolved": None,
            "vision_capable": False,
            "installed_models": [],
            "vision_capable_installed": [],
            "suggested_pull": VISION_MODEL_DEFAULT,
            "families_supported": list(_VISION_FAMILIES),
        }
    installed = await ollama.installed_models()
    ollama.reset_vision_cache()
    resolved = await ollama.resolve_vision_model()
    vision_caps = [m for m in installed if any(m.lower().startswith(f) for f in _VISION_FAMILIES)]
    return {
        "ollama_configured": True,
        "vision_model_config": ollama.vision_model_config,
        "vision_model_resolved": resolved,
        "vision_capable": bool(resolved),
        "installed_models": installed,
        "vision_capable_installed": vision_caps,
        "suggested_pull": VISION_MODEL_DEFAULT,
        "families_supported": list(_VISION_FAMILIES),
    }


@router.post("/pull")
async def vision_pull(req: VisionPullRequest, request: Request) -> StreamingResponse:
    """Stream POST /api/pull progress straight back to the browser. Each
    line is a JSON event from ollama (`{"status":"downloading","total":..,
    "completed":..}`) — the frontend renders it as a progress bar.

    Used by the onboarding wizard's "Install vision model" button and by
    the Settings → Integrations → Ollama vision card."""
    ollama = getattr(request.app.state, "ollama", None)
    if ollama is None or not ollama.is_configured():
        raise HTTPException(503, detail="ollama not configured")

    async def gen():
        try:
            async for line in ollama.pull_model(req.name):
                yield (line + "\n").encode("utf-8")
        except OllamaError as e:
            err = json.dumps({"error": safe_error(e)})
            yield (err + "\n").encode("utf-8")

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@router.post("/check")
async def vision_check(req: VisionCheckRequest, request: Request) -> dict[str, Any]:
    ollama = request.app.state.ollama
    if not ollama.is_configured():
        raise HTTPException(503, detail="ollama not configured")
    # SSRF guard: image_url is fetched server-side. Only allow the
    # configured Plex / Tautulli thumbnail hosts; everything else must
    # arrive as image_b64.
    if req.image_url and not req.image_b64:
        if not _image_url_allowed(req.image_url, _allowed_image_hosts()):
            raise HTTPException(
                400,
                detail=(
                    "image_url host not allowed — must be the configured "
                    "Plex or Tautulli host, or pass image_b64 instead"
                ),
            )
    try:
        # #232: pass model=None so resolve_vision_model() runs and we get
        # honest "vision pre-filter unavailable" instead of a text-model
        # hallucination. Caller can still override explicitly via req.model.
        raw = await ollama.vision_describe(
            image_url=req.image_url,
            image_b64=req.image_b64,
            prompt=req.prompt or PROMPT,
            model=req.model,  # None -> resolve_vision_model()
        )
    except OllamaError as e:
        msg = str(e)
        # #232: a missing vision model is NOT a backend failure — it's a
        # missing prerequisite. 503 with the actionable hint, not 502.
        if "no vision-capable model" in msg:
            raise HTTPException(503, detail=msg)
        raise HTTPException(502, detail=msg)
    # Best-effort JSON parse from the model output
    parsed: dict[str, Any] = {}
    try:
        # Strip code fences if model wrapped output
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```", 2)[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        parsed = {"raw_response": raw, "parse_error": True}
    # Compute skip_recommended from the structured fields
    has_hard = bool(parsed.get("has_hardsubs"))
    density = float(parsed.get("dialog_density") or 0.5) if not parsed.get("parse_error") else 0.5
    skip = has_hard or density < 0.15
    parsed["skip_recommended"] = skip
    return parsed
