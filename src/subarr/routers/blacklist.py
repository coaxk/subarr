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

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ..integrations import IntegrationError

router = APIRouter(prefix="/api/blacklist", tags=["blacklist"])
log = logging.getLogger(__name__)


def _shape_history_row(r: dict[str, Any], media_type: str) -> dict[str, Any]:
    """#317: flatten one Bazarr history row to what the UI needs to show a
    downloaded sub and (if eligible) blacklist it. A row is blacklistable only
    if it came from a provider (has provider + subs_id) and isn't already
    blacklisted — manual uploads carry subs_id=null. The row already holds every
    field the blacklist POST needs, so no path-correlation is required."""
    lang = r.get("language") or {}
    code = lang.get("code2") if isinstance(lang, dict) else (lang or None)
    provider = r.get("provider")
    subs_id = r.get("subs_id")
    already = bool(r.get("blacklisted"))
    out: dict[str, Any] = {
        "provider": provider,
        "subs_id": subs_id,
        "language": code,
        "language_name": lang.get("name") if isinstance(lang, dict) else None,
        "subtitles_path": r.get("subtitles_path"),
        "score": r.get("score"),
        "description": r.get("description"),
        "timestamp": r.get("timestamp"),
        "blacklisted": already,
        "blacklistable": bool(provider and subs_id and not already),
    }
    if media_type == "episode":
        out["series_id"] = r.get("sonarrSeriesId")
        out["episode_id"] = r.get("sonarrEpisodeId")
    else:
        out["radarr_id"] = r.get("radarrId")
    return out


@router.get("/history")
async def blacklist_history(
    request: Request,
    media_type: str = Query(..., pattern="^(episode|movie)$"),
    id: int = Query(..., description="sonarrEpisodeId for episodes, radarrId for movies"),
) -> dict[str, Any]:
    """#317: a file's Bazarr subtitle-download history, so the user can see
    which provider supplied each sub and blacklist a bad one (the blacklist
    button forwards the row's identifiers to POST /api/blacklist/{episode,movie})."""
    bazarr = request.app.state.integrations.bazarr
    if not bazarr.is_configured():
        raise HTTPException(503, detail="bazarr not configured")
    try:
        if media_type == "episode":
            rows = await bazarr.episodes_history(sonarr_episode_id=id)
        else:
            rows = await bazarr.movies_history(radarr_movie_id=id)
    except IntegrationError as e:
        raise HTTPException(502, detail=str(e))
    return {"subtitles": [_shape_history_row(r, media_type) for r in (rows or [])]}


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
