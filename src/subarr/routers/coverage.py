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


# Score values that suppress a row from "actually needs queuing" view.
# Treated as "subarr/probe confirmed English present" rows.
_SUPPRESSED_EMBEDDED = {"EN", "EN(SDH)"}


@router.get("/coverage")
async def get_coverage(
    request: Request,
    fresh: bool = Query(False, description="Bypass the 60s cache"),
    tautulli: bool = Query(True, description="Include Tautulli scoring"),
    probe: bool = Query(True, description="Fold ProbeStore embedded-sub data into rows"),
    hide_embedded_en: bool = Query(
        True,
        description=(
            "Hide rows where our probe confirmed an embedded English track "
            "(full or SDH). Default True — these aren't real gaps. Set False "
            "to see them, e.g. when diagnosing Bazarr-probe disagreements."
        ),
    ),
    hide_stale_disk: bool = Query(
        True,
        description=(
            "Hide rows where subarr's recursive scan found a sibling .srt on "
            "disk that Bazarr doesn't know about yet (common when subgen "
            "wrote it out-of-band). Default True — same logic as "
            "hide_embedded_en. Set False to see them + use the →Bazarr "
            "button to trigger Bazarr's scan-disk task."
        ),
    ),
) -> dict[str, Any]:
    cache_key = (
        f"tautulli={tautulli}&probe={probe}"
        f"&hide_emb={hide_embedded_en}&hide_stale={hide_stale_disk}"
    )
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

    if hide_embedded_en or hide_stale_disk:
        items = body["items"]
        original = len(items)
        kept = []
        emb_dropped = stale_dropped = 0
        for it in items:
            if hide_embedded_en and it.get("embedded_en") in _SUPPRESSED_EMBEDDED:
                emb_dropped += 1
                continue
            if hide_stale_disk and it.get("has_sub_on_disk"):
                stale_dropped += 1
                continue
            kept.append(it)
        body["items"] = kept
        body["totals"]["items"] = len(kept)
        body["totals"]["suppressed_by_embedded_en"] = emb_dropped
        body["totals"]["suppressed_by_stale_disk"] = stale_dropped
        body["totals"]["episodes"] = sum(1 for i in kept if i["media_type"] == "episode")
        body["totals"]["movies"] = sum(1 for i in kept if i["media_type"] == "movie")

    body["cached"] = False
    _cache[cache_key] = (now, body)
    return body
