"""Admin endpoints: container restart, Plex library refresh."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..config import settings
from ..docker_client import DockerUnavailable
from ..integrations import IntegrationError

router = APIRouter(prefix="/api", tags=["admin"])
log = logging.getLogger(__name__)


@router.post("/restart")
async def restart_subgen(request: Request) -> dict:
    docker_ops = request.app.state.docker
    try:
        await docker_ops.restart_subgen()
    except DockerUnavailable as e:
        raise HTTPException(503, detail=str(e))
    try:
        info = await docker_ops.container_info()
    except DockerUnavailable as e:
        # restart succeeded; info failed — surface what we know
        return {"restarted": True, "warning": str(e)}
    return {"restarted": True, "container": info}


@router.get("/container")
async def container(request: Request) -> dict:
    docker_ops = request.app.state.docker
    try:
        return await docker_ops.container_info()
    except DockerUnavailable as e:
        raise HTTPException(503, detail=str(e))


@router.post("/plex/scan")
async def plex_scan(request: Request) -> dict:
    """Full scan against PLEX_SECTION ("all" by default). The integration-aware
    PlexClient handles the request shape; we just surface the result."""
    plex = request.app.state.integrations.plex
    if not plex.is_configured():
        raise HTTPException(503, detail="Plex not configured (PLEX_URL/PLEX_TOKEN)")
    try:
        return await plex.full_scan()
    except IntegrationError as e:
        raise HTTPException(502, detail=str(e))


class PartialScanRequest(BaseModel):
    # Absolute or canonical (relative to media_root) path to a file. Directory
    # is derived inside the client. Mostly used internally by the completion
    # watcher; exposed as an endpoint for manual triggering / testing.
    path: str


@router.post("/plex/partial-scan")
async def plex_partial_scan(req: PartialScanRequest, request: Request) -> dict:
    """v1.1.1: trigger a Plex partial scan targeting one file's directory.

    Accepts either an absolute path on subarr's filesystem view or a canonical
    path relative to media_root. Path translation (PLEX_PATH_PREFIX) and
    section discovery happen inside PlexClient. Closes the Apple TV loop:
    once subarr writes a sidecar, fire this and Plex picks it up immediately
    instead of waiting for its next periodic full scan."""
    plex = request.app.state.integrations.plex
    if not plex.is_configured():
        raise HTTPException(503, detail="Plex not configured (PLEX_URL/PLEX_TOKEN)")
    from pathlib import Path
    p = req.path
    # Treat anything that isn't absolute as canonical-relative-to-media_root.
    if not p.startswith("/"):
        p = str(settings.media_root / Path(p))
    try:
        return await plex.partial_scan(p)
    except IntegrationError as e:
        raise HTTPException(502, detail=str(e))


@router.get("/plex/sections")
async def plex_sections(request: Request) -> dict:
    """List Plex sections (id, title, paths). Useful in Settings UI for
    picking a section + verifying path translation is wired right."""
    plex = request.app.state.integrations.plex
    if not plex.is_configured():
        raise HTTPException(503, detail="Plex not configured (PLEX_URL/PLEX_TOKEN)")
    try:
        return {"sections": await plex.sections(refresh=True)}
    except IntegrationError as e:
        raise HTTPException(502, detail=str(e))
