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

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..integrations.ollama import OllamaError

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


@router.post("/check")
async def vision_check(req: VisionCheckRequest, request: Request) -> dict[str, Any]:
    ollama = request.app.state.ollama
    if not ollama.is_configured():
        raise HTTPException(503, detail="ollama not configured")
    try:
        raw = await ollama.vision_describe(
            image_url=req.image_url,
            image_b64=req.image_b64,
            prompt=req.prompt or PROMPT,
            model=req.model or VISION_MODEL_DEFAULT,
        )
    except OllamaError as e:
        raise HTTPException(502, detail=str(e))
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
