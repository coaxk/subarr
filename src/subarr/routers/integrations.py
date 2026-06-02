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
        if summary_kind == "ollama_models":
            # Ollama uses /api/tags as both liveness probe + model list.
            # Surface model count + first-5 names for the Settings panel.
            tags = await client.tags()
            models = tags.get("models", []) if isinstance(tags, dict) else []
            # #232: surface vision-pre-filter capability so the Settings
            # panel can show "Vision pre-filter active / inactive" with
            # the resolved model name, instead of users discovering it
            # only when a vision call fails.
            client.reset_vision_cache()
            vision_resolved = await client.resolve_vision_model()
            return {
                "name": name,
                "online": True,
                "configured": True,
                "badges": {
                    "models": len(models),
                    "model_names": ", ".join(m.get("name", "?") for m in models[:3])
                                   + (" ..." if len(models) > 3 else ""),
                    "vision_model_config": client.vision_model_config,
                    "vision_model_resolved": vision_resolved or "",
                    "vision_capable": bool(vision_resolved),
                },
            }
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
    # Pull Ollama from app.state — it's not part of the IntegrationBundle
    # (used only by lang_enrichment, not coverage flow). Treating it like
    # a first-class integration here so the Settings panel can show its
    # model list + reachability without us inventing a separate endpoint.
    ollama = getattr(request.app.state, "ollama", None)
    probes_coros = [
        _probe("bazarr", integrations.bazarr, "bazarr_badges"),
        _probe("sonarr", integrations.sonarr),
        _probe("radarr", integrations.radarr),
        _probe("plex", integrations.plex),
        _probe("tautulli", integrations.tautulli),
    ]
    if ollama is not None:
        probes_coros.append(_probe("ollama", ollama, "ollama_models"))
    probes = await asyncio.gather(*probes_coros)
    # Subgen capabilities probed once at boot, cached on app.state.
    # Surfaced here so the UI can show "compat mode" badges + gate
    # scan-submit on has_batch.
    caps = getattr(request.app.state, "subgen_caps", None)
    subgen_block = caps.to_dict() if caps else {"reachable": False}
    subgen_block["name"] = "subgen"
    return {"integrations": probes, "subgen": subgen_block}
