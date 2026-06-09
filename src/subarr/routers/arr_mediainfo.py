"""v1.1-A: arr-mediaInfo sync endpoints.

POST /api/probe/arr-sync — pulls Sonarr+Radarr episode/movie file rows
and hydrates the probe_store from their embedded mediaInfo blocks. Cheap
backstop that skips ffprobe on files arr already mediainfo'd.

GET /api/probe/sources — returns the count split by source for the UI.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

from ..arr_mediainfo import ArrMediainfoSync

router = APIRouter(prefix="/api", tags=["probe"])
log = logging.getLogger(__name__)


@router.post("/probe/arr-sync")
async def arr_sync(request: Request) -> dict[str, Any]:
    state = request.app.state
    sync = ArrMediainfoSync(bundle=state.integrations, probe_store=state.probe_store)
    return await sync.run()


@router.get("/probe/sources")
async def probe_sources(request: Request) -> dict[str, Any]:
    state = request.app.state
    return {"counts": state.probe_store.count_by_source()}
