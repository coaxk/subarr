"""#633: choose transcribe vs translate per file.

GET  /api/per-file-task/config   the setting, plus whether the connected subgen allows it
POST /api/per-file-task/config   persist {enabled, wanted}
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import config, config_store
from ..per_file_task import known_language, normalize_wanted

router = APIRouter(prefix="/api/per-file-task", tags=["per-file-task"])


class PerFileTaskConfig(BaseModel):
    enabled: bool
    wanted: list[str]


def _unavailable_reason(caps: Any) -> str | None:
    """Why the connected subgen cannot run the feature, or None when it can.
    The reason names the one missing capability."""
    if caps is None:
        return "subgen capabilities have not been read yet"
    if not getattr(caps, "per_request_task", False):
        return "subgen does not accept a per-request task"
    if getattr(caps, "preferred_audio_languages", None) is None:
        return "subgen does not advertise its preferred audio languages; update subarr-subgen to v4.38+"
    unreadable = getattr(caps, "preferred_audio_languages_unreadable", ())
    if unreadable:
        return "subgen advertised a preferred audio language subarr cannot read: " + ", ".join(unreadable)
    return None


def _status(request: Request) -> dict:
    caps = getattr(request.app.state, "subgen_caps", None)
    reason = _unavailable_reason(caps)
    preferred = getattr(caps, "preferred_audio_languages", None)
    return {
        "enabled": bool(config.settings.per_file_task_enabled),
        "wanted": list(config.settings.per_file_task_wanted),
        "available": reason is None,
        "unavailable_reason": reason,
        "preferred_audio_languages": list(preferred) if preferred is not None else None,
        "force_detected_language_to": getattr(caps, "force_detected_language_to", None),
    }


@router.get("/config")
def get_config(request: Request) -> dict:
    return _status(request)


@router.post("/config")
def set_config(body: PerFileTaskConfig, request: Request) -> dict:
    """Validate, persist (survives a restart) and apply live. Saving while the
    connected subgen cannot run the feature is allowed: the choice is kept and
    GET reports `available: false`."""
    unknown = [w for w in body.wanted if w and w.strip() and known_language(w) is None]
    if unknown:
        raise HTTPException(400, detail=f"not a language code subarr knows: {', '.join(unknown)}")
    wanted = normalize_wanted(body.wanted)
    if body.enabled and not wanted:
        raise HTTPException(400, detail="enter at least one wanted subtitle language to turn this on")
    config_store.save_override("per_file_task_enabled", body.enabled)
    config_store.save_override("per_file_task_wanted", wanted)
    # An operator-pinned env var stays authoritative live (the choice is still
    # saved); the response below reports what is actually in effect.
    if not config.env_is_set("per_file_task_enabled"):
        object.__setattr__(config.settings, "per_file_task_enabled", body.enabled)
    if not config.env_is_set("per_file_task_wanted"):
        object.__setattr__(config.settings, "per_file_task_wanted", wanted)
    return _status(request)
