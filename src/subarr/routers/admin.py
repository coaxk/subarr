"""Admin endpoints: container restart, Plex library refresh."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..docker_client import DockerUnavailable
from ..error_detail import safe_error
from ..integrations import IntegrationError

router = APIRouter(prefix="/api", tags=["admin"])
log = logging.getLogger(__name__)


@router.get("/ui-bootstrap")
async def ui_bootstrap(request: Request) -> dict:
    """#198: hand the configured API key to the bundled frontend, but ONLY
    to scripts running on a page we served. Gated on Sec-Fetch-Site ==
    same-origin: a cross-site page can neither set that header nor read this
    response (CORS). Returns an empty key when none is configured (the UI
    then sends no X-Api-Key, which is correct — the middleware is a no-op)."""
    from ..config import settings

    sfs = request.headers.get("sec-fetch-site", "").lower()
    if sfs and sfs != "same-origin":
        raise HTTPException(403, detail="ui-bootstrap is same-origin only")
    # Header-less requests (no Sec-Fetch-Site at all) are direct hits, not
    # same-origin page fetches — also denied so curl can't lift the key.
    if not sfs:
        raise HTTPException(403, detail="ui-bootstrap is same-origin only")
    return {"api_key": settings.api_key}


def _auth_is_configured(api_key: str, auth_user: str, auth_pass: str) -> bool:
    """True if ANY authentication is configured: an API key, or a COMPLETE
    HTTP Basic pair (a half-set user-without-pass is not valid auth)."""
    return bool(api_key) or bool(auth_user and auth_pass)


@router.get("/auth-status")
async def auth_status(request: Request) -> dict:
    """Whether any authentication is configured. Backs the dashboard's no-auth
    warning banner (#238-A). Leaks no secret. Under forced auth (#238) this is
    also True once an admin credential is set OR auth is delegated to a proxy
    (SUBARR_AUTH_DISABLED) — so the banner doesn't false-nag a properly-secured
    install."""
    from ..config import settings

    if settings.auth_disabled:  # delegated to a reverse proxy — not exposure
        return {"configured": True}
    store = getattr(request.app.state, "auth_store", None)
    has_cred = bool(store and store.has_credential())
    configured = has_cred or _auth_is_configured(settings.api_key, settings.auth_user, settings.auth_pass)
    return {"configured": configured}


@router.post("/restart")
async def restart_subgen(request: Request) -> dict:
    docker_ops = request.app.state.docker
    try:
        await docker_ops.restart_subgen()
    except DockerUnavailable as e:
        raise HTTPException(503, detail=safe_error(e))
    try:
        info = await docker_ops.container_info()
    except DockerUnavailable as e:
        # restart succeeded; info failed — surface what we know
        return {"restarted": True, "warning": safe_error(e)}
    return {"restarted": True, "container": info}


@router.get("/container")
async def container(request: Request) -> dict:
    docker_ops = request.app.state.docker
    try:
        return await docker_ops.container_info()
    except DockerUnavailable as e:
        raise HTTPException(503, detail=safe_error(e))


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
        raise HTTPException(502, detail=safe_error(e))


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
    p = req.path
    # Treat anything that isn't absolute as a canonical (library-aware, #134).
    if not p.startswith("/"):
        from ..paths import canonical_to_fs

        p = str(canonical_to_fs(p))
    try:
        return await plex.partial_scan(p)
    except IntegrationError as e:
        raise HTTPException(502, detail=safe_error(e))


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
        raise HTTPException(502, detail=safe_error(e))
