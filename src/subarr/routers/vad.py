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
from pydantic import BaseModel

from .. import config, config_store, vad

router = APIRouter(prefix="/api/vad", tags=["vad"])
log = logging.getLogger(__name__)


class VadConfig(BaseModel):
    enabled: bool


@router.get("/status")
def status() -> dict:
    """What the onboarding/settings UI needs to render the toggle: is it
    switched on, is the runtime baked in, and has the model been pulled.
    `env_controlled` = an explicit SUBARR_VAD_ENABLED pins it (operator
    authoritative) → the UI renders the toggle as locked."""
    model = vad._model_path()
    return {
        "enabled": bool(getattr(config.settings, "vad_enabled", False)),
        "runtime_present": vad.runtime_present(),
        "model_present": model is not None,
        "available": vad.vad_available(),
        "model_path": model,
        "env_controlled": config.env_is_set("vad_enabled"),
    }


@router.post("/config")
def set_config(body: VadConfig) -> dict:
    """Persist the enable/disable choice (survives restart via #112) and patch
    the running Settings so it takes effect immediately. If the operator pinned
    SUBARR_VAD_ENABLED, env stays authoritative live (we still persist the
    preference, but env wins on reload)."""
    config_store.save_override("vad_enabled", body.enabled)
    if not config.env_is_set("vad_enabled"):
        object.__setattr__(config.settings, "vad_enabled", body.enabled)
    return status()


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
