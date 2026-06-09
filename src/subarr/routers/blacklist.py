"""v1.1-N: Bazarr blacklist endpoints.

POST /api/blacklist/episode — blacklist a desync'd episode sub
POST /api/blacklist/movie   — blacklist a desync'd movie sub

Both forward to Bazarr's /api/{episodes|movies}/blacklist. Useful flow:
user marks a sub as bad in subarr's Activity view → we tell Bazarr →
Bazarr stops refetching the same broken release on future searches.

Auto-detection (offset >1s vs audio) ships in v1.1.1 alongside the Layer 3
Whisper endpoint, since reliable detection needs speech-segment knowledge.
For v1.1 this is manual / UI-driven.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..integrations import IntegrationError

router = APIRouter(prefix="/api/blacklist", tags=["blacklist"])
log = logging.getLogger(__name__)


class EpisodeBlacklistRequest(BaseModel):
    series_id: int
    episode_id: int
    provider: str
    subs_id: str
    language: str = "en"
    subtitles_path: str
    reason: str | None = None  # user-supplied note (stored locally for telemetry later)


class MovieBlacklistRequest(BaseModel):
    radarr_id: int
    provider: str
    subs_id: str
    language: str = "en"
    subtitles_path: str
    reason: str | None = None


@router.post("/episode")
async def blacklist_episode(req: EpisodeBlacklistRequest, request: Request) -> dict[str, Any]:
    bazarr = request.app.state.integrations.bazarr
    if not bazarr.is_configured():
        raise HTTPException(503, detail="bazarr not configured")
    try:
        result = await bazarr.blacklist_episode(
            series_id=req.series_id,
            episode_id=req.episode_id,
            provider=req.provider,
            subs_id=req.subs_id,
            language=req.language,
            subtitles_path=req.subtitles_path,
        )
    except IntegrationError as e:
        raise HTTPException(502, detail=str(e))
    return {"blacklisted": True, "reason": req.reason, "bazarr_response": result}


@router.post("/movie")
async def blacklist_movie(req: MovieBlacklistRequest, request: Request) -> dict[str, Any]:
    bazarr = request.app.state.integrations.bazarr
    if not bazarr.is_configured():
        raise HTTPException(503, detail="bazarr not configured")
    try:
        result = await bazarr.blacklist_movie(
            radarr_id=req.radarr_id,
            provider=req.provider,
            subs_id=req.subs_id,
            language=req.language,
            subtitles_path=req.subtitles_path,
        )
    except IntegrationError as e:
        raise HTTPException(502, detail=str(e))
    return {"blacklisted": True, "reason": req.reason, "bazarr_response": result}
