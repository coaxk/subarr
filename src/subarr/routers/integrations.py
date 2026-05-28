"""GET /api/integrations/health — per-upstream up/down + version + summary.

One concurrent fan-out call. Per-upstream errors don't fail the whole
endpoint — each integration reports its own status. The frontend's
Settings tab renders this as a status grid.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Request

from ..integrations import IntegrationError

router = APIRouter(prefix="/api", tags=["integrations"])
log = logging.getLogger(__name__)


async def _probe(name: str, client, summary_kind: str = "version") -> dict[str, Any]:
    if not client.is_configured():
        return {"name": name, "online": False, "configured": False}
    try:
        if summary_kind == "bazarr_badges":
            status_task = asyncio.create_task(client.status())
            badges_task = asyncio.create_task(client.badges())
            status = await status_task
            badges = await badges_task
            return {
                "name": name,
                "online": True,
                "configured": True,
                "version": status.get("bazarr_version") or status.get("version"),
                "badges": badges,
            }
        status = await client.status()
        return {
            "name": name,
            "online": True,
            "configured": True,
            "version": status.get("version") if isinstance(status, dict) else None,
        }
    except IntegrationError as e:
        return {"name": name, "online": False, "configured": True, "error": str(e)}
    except Exception as e:  # defensive
        log.warning("%s probe unexpected error: %s", name, e)
        return {"name": name, "online": False, "configured": True, "error": repr(e)}


@router.get("/integrations/health")
async def integrations_health(request: Request) -> dict[str, Any]:
    integrations = request.app.state.integrations
    probes = await asyncio.gather(
        _probe("bazarr", integrations.bazarr, "bazarr_badges"),
        _probe("sonarr", integrations.sonarr),
        _probe("radarr", integrations.radarr),
        _probe("tautulli", integrations.tautulli),
    )
    # Subgen capabilities probed once at boot, cached on app.state.
    # Surfaced here so the UI can show "compat mode" badges + gate
    # scan-submit on has_batch.
    caps = getattr(request.app.state, "subgen_caps", None)
    subgen_block = caps.to_dict() if caps else {"reachable": False}
    subgen_block["name"] = "subgen"
    return {"integrations": probes, "subgen": subgen_block}
