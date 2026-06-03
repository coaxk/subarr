"""#111 — speech-aware audio (silero VAD) status + opt-in model pull.

The onnxruntime runtime is baked into the image; this endpoint pulls the
small (~2MB) silero model on the user's explicit opt-in (onboarding
checkbox). Download is from a pinned URL, SHA256-verified, written
atomically — see `vad.pull_model`.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from .. import config, vad

router = APIRouter(prefix="/api/vad", tags=["vad"])
log = logging.getLogger(__name__)


@router.get("/status")
def status() -> dict:
    """What the onboarding/settings UI needs to render the toggle: is it
    switched on, is the runtime baked in, and has the model been pulled."""
    model = vad._model_path()
    return {
        "enabled": bool(getattr(config.settings, "vad_enabled", False)),
        "runtime_present": vad.runtime_present(),
        "model_present": model is not None,
        "available": vad.vad_available(),
        "model_path": model,
    }


@router.post("/pull-model")
async def pull_model() -> dict:
    """Pull the pinned silero model on opt-in. Idempotent; runs off the event
    loop. Surfaces a clean error if the runtime/network/checksum fails so the
    UI can show it (the app still works — it just falls back to silencedetect)."""
    if not vad.runtime_present():
        raise HTTPException(503, detail="VAD runtime (onnxruntime) is not installed in this image")
    try:
        result = await asyncio.to_thread(vad.pull_model)
    except Exception as e:  # network / checksum / unpinned-hash / disk
        log.warning("VAD model pull failed", exc_info=True)
        raise HTTPException(502, detail=f"model pull failed: {e}")
    return result
