"""Admin endpoints: container restart, Plex library refresh."""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException, Request

from ..config import settings
from ..docker_client import DockerUnavailable

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
async def plex_scan() -> dict:
    if not settings.plex_token:
        raise HTTPException(503, detail="PLEX_TOKEN not configured")
    url = f"{settings.plex_url.rstrip('/')}/library/sections/{settings.plex_section}/refresh"
    params = {"X-Plex-Token": settings.plex_token}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, params=params)
    except httpx.HTTPError as e:
        raise HTTPException(503, detail=f"plex unreachable: {e}")
    if r.status_code >= 400:
        raise HTTPException(502, detail=f"plex returned {r.status_code}: {r.text[:200]}")
    return {"triggered": True, "section": settings.plex_section, "plex_status": r.status_code}
