"""v1.1-J: Bazarr provider success leaderboard endpoints.

GET /api/providers/leaderboard            — full table
GET /api/providers/leaderboard/telemetry  — anonymous snapshot for subarr.com/stats
POST /api/providers/{name}/reset          — kick a throttled provider
"""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from ..integrations import IntegrationError
from ..provider_leaderboard import build_leaderboard, telemetry_snapshot

router = APIRouter(prefix="/api/providers", tags=["providers"])
log = logging.getLogger(__name__)

_CACHE_TTL = 600  # 10 min; leaderboard is roll-up, doesn't need sub-minute refresh
_cache: dict[str, Any] = {"at": 0, "data": None}


@router.get("/leaderboard")
async def get_leaderboard(request: Request,
                          fresh: bool = Query(False)) -> dict[str, Any]:
    now = time.time()
    if not fresh and _cache["data"] and (now - _cache["at"]) < _CACHE_TTL:
        out = dict(_cache["data"])
        out["cached"] = True
        out["cache_age_s"] = round(now - _cache["at"], 1)
        return out
    bazarr = request.app.state.integrations.bazarr
    try:
        data = await build_leaderboard(bazarr)
    except IntegrationError as e:
        raise HTTPException(502, detail=str(e))
    _cache["at"] = now
    _cache["data"] = data
    data["cached"] = False
    return data


@router.get("/leaderboard/telemetry")
async def get_telemetry(request: Request) -> dict[str, Any]:
    """Anonymous snapshot for cross-user aggregation. Drops titles/paths;
    keeps per-provider success metrics. Hook subarr.com/stats here."""
    if _cache["data"]:
        return telemetry_snapshot(_cache["data"])
    bazarr = request.app.state.integrations.bazarr
    try:
        data = await build_leaderboard(bazarr)
    except IntegrationError as e:
        raise HTTPException(502, detail=str(e))
    return telemetry_snapshot(data)
