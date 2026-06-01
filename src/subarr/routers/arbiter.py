"""v1.1-F: Whisper-or-Bazarr arbiter — ask Bazarr's providers for human
subs before queueing Whisper.

GET /api/arbiter/candidates?episode_id= → list candidates with provider,
    score, release_info. UI surfaces top N and lets the user pick.
POST /api/arbiter/accept → tell Bazarr to download a specific candidate
    (closes the loop without Whisper).

Score threshold guidance: Bazarr providers report scores 0–360, where:
  - 360 = perfect match (right release group, year, source)
  - 300+ = excellent fit (likely correct)
  - 240+ = decent fit
  - <240 = take with caution
We surface the top 5 and let the user decide. UI can default-recommend
"take it" if best score >= 300.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ..integrations import IntegrationError

router = APIRouter(prefix="/api/arbiter", tags=["arbiter"])
log = logging.getLogger(__name__)


@router.get("/candidates")
async def list_candidates(
    request: Request,
    episode_id: int | None = Query(None),
    movie_id: int | None = Query(None),
    language: str = Query("en"),
) -> dict[str, Any]:
    if not episode_id and not movie_id:
        raise HTTPException(400, detail="episode_id or movie_id required")
    bazarr = request.app.state.integrations.bazarr
    if not bazarr.is_configured():
        raise HTTPException(503, detail="bazarr not configured")
    try:
        if episode_id:
            rows = await bazarr.candidate_episode_subtitles(episode_id, language=language)
        else:
            rows = await bazarr.candidate_movie_subtitles(movie_id, language=language)
    except IntegrationError as e:
        raise HTTPException(502, detail=str(e))
    # v1.1-F fix: filter out 'whisperai' provider when our own subgen is
    # configured — Bazarr's whisperai provider IS subgen, so showing it as
    # a candidate creates a confusing double-positive ("Take whisperai"
    # vs "Whisper anyway" mean the same thing). Filter so the arbiter
    # only surfaces genuinely DIFFERENT options (human-translated providers).
    subgen_configured = bool(getattr(request.app.state, "subgen", None))
    filtered_self_whisper = 0
    if subgen_configured:
        new_rows = []
        for r in rows:
            if (r.get("provider") or "").lower() in ("whisperai", "whisper"):
                filtered_self_whisper += 1
                continue
            new_rows.append(r)
        rows = new_rows
    # Annotate each row with a confidence tier the UI can colour-code.
    for r in rows:
        try:
            score = int(r.get("score", 0))
        except (TypeError, ValueError):
            score = 0
        if score >= 300:
            r["tier"] = "excellent"
        elif score >= 240:
            r["tier"] = "decent"
        else:
            r["tier"] = "weak"
    return {
        "count": len(rows), "language": language,
        "candidates": rows[:20],
        "filtered_self_whisper": filtered_self_whisper,
    }


class AcceptRequest(BaseModel):
    episode_id: int | None = None
    movie_id: int | None = None
    language: str = "en"
    provider: str
    subtitles_id: str
    score: int
    forced: bool = False
    hi: bool = False


@router.post("/accept")
async def accept_candidate(req: AcceptRequest, request: Request) -> dict[str, Any]:
    if not req.episode_id and not req.movie_id:
        raise HTTPException(400, detail="episode_id or movie_id required")
    bazarr = request.app.state.integrations.bazarr
    if not bazarr.is_configured():
        raise HTTPException(503, detail="bazarr not configured")
    try:
        if req.episode_id:
            result = await bazarr.download_episode_candidate(
                episode_id=req.episode_id,
                language=req.language,
                provider=req.provider,
                subtitles_id=req.subtitles_id,
                score=req.score,
                forced=req.forced,
                hi=req.hi,
            )
        else:
            result = await bazarr.download_movie_candidate(
                movie_id=req.movie_id,
                language=req.language,
                provider=req.provider,
                subtitles_id=req.subtitles_id,
                score=req.score,
                forced=req.forced,
                hi=req.hi,
            )
    except IntegrationError as e:
        raise HTTPException(502, detail=str(e))
    return {"accepted": True, "bazarr_response": result}
