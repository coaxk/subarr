"""GET /api/coverage — prioritised gap list.

Heavy endpoint (touches 4 upstream services + filesystem). Process-local
TTL cache keyed by (tautulli, probe) since each combo yields different
output. Cache is bypassable via ?fresh=true.

?probe=true folds the ProbeStore cache into each row (embedded_en,
audio_langs, suggest_bazarr_rescan). Default is True now — the cache
lookup is cheap and the embedded-EN reconciliation is the v1.1 hotfix's
whole point.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Query, Request

from ..coverage_engine import CoverageReport, build_coverage

router = APIRouter(prefix="/api", tags=["coverage"])
log = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 60
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


@router.get("/coverage")
async def get_coverage(
    request: Request,
    fresh: bool = Query(False, description="Bypass the 60s cache"),
    tautulli: bool = Query(True, description="Include Tautulli scoring"),
    probe: bool = Query(True, description="Fold ProbeStore embedded-sub data into rows"),
) -> dict[str, Any]:
    cache_key = f"tautulli={tautulli}&probe={probe}"
    now = time.time()
    if not fresh:
        entry = _cache.get(cache_key)
        if entry and (now - entry[0]) < _CACHE_TTL_SECONDS:
            cached = dict(entry[1])
            cached["cached"] = True
            cached["cache_age_s"] = round(now - entry[0], 1)
            return cached

    bundle = request.app.state.integrations
    probe_store = request.app.state.probe_store if probe else None
    report: CoverageReport = await build_coverage(
        bundle, use_tautulli=tautulli, probe_store=probe_store,
    )
    body = report.to_dict()
    body["cached"] = False
    _cache[cache_key] = (now, body)
    return body
