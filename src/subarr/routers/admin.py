"""Admin endpoints: container restart, Plex library refresh, db maintenance."""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..docker_client import DockerUnavailable
from ..error_detail import safe_error
from ..integrations import IntegrationError

router = APIRouter(prefix="/api", tags=["admin"])
log = logging.getLogger(__name__)


@router.get("/ui-bootstrap")
async def ui_bootstrap(request: Request) -> dict:
    """#198/#329: hand the configured API key to the bundled frontend, but ONLY
    to scripts running on a page we served.

    Primary gate is Sec-Fetch-Site == same-origin (a cross-site page can neither
    forge that header nor read this response under CORS). #329: some reverse
    proxies (unraid WebUI, etc.) strip fetch-metadata headers, which used to 403
    every proxied install that set SUBARR_API_KEY — no key reached the UI, so
    every authenticated /api call 401'd and the whole UI broke. So when
    Sec-Fetch-Site is ABSENT we fall back to a same-origin Referer/Origin match.
    A genuine cross-site fetch still carries Sec-Fetch-Site: cross-site (rejected
    below) and a Referer on the attacker's host — the cross-site-JS defence is
    intact; only a header-less direct hit with a forged Referer slips through,
    and that caller already has direct network access to the API anyway."""
    from ..config import settings

    # Nothing to protect when no key is configured — return empty unconditionally
    # so keyless installs behind any proxy get a clean 200, not a console 403.
    if not settings.api_key:
        return {"api_key": ""}

    sfs = request.headers.get("sec-fetch-site", "").lower()
    if sfs == "same-origin":
        return {"api_key": settings.api_key}
    if sfs:
        # An explicit non-same-origin browser fetch (cross-site / same-site /
        # none) — never hand out the key.
        raise HTTPException(403, detail="ui-bootstrap is same-origin only")
    # Sec-Fetch-Site absent: likely a proxy that stripped fetch-metadata. Accept
    # a same-origin Referer/Origin; otherwise it's a header-less direct hit.
    if _referer_is_same_origin(request):
        return {"api_key": settings.api_key}
    raise HTTPException(403, detail="ui-bootstrap is same-origin only")


def _referer_is_same_origin(request: Request) -> bool:
    """True when the request's Origin/Referer host matches the host the client
    addressed (X-Forwarded-Host if a proxy set it, else Host). Lets proxied
    installs that drop Sec-Fetch-Site through without weakening the cross-site
    defence — a cross-site page's Referer carries the attacker's host."""
    from urllib.parse import urlsplit

    ref = request.headers.get("origin") or request.headers.get("referer") or ""
    if not ref:
        return False
    ref_host = urlsplit(ref).netloc.rsplit("@", 1)[-1].lower()
    fwd = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    host = fwd.split(",")[0].strip().lower()
    return bool(ref_host) and ref_host == host


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


# ─── #291 Slice B — on-demand db maintenance ─────────────────────────────────


@router.post("/admin/db/integrity-check")
async def db_integrity_check(request: Request) -> dict:
    """Full PRAGMA integrity_check (index↔table cross-check).

    Heavier than the boot-time quick_check — can take seconds on a large
    database, so it runs in a thread. Returns ok=True and findings=['ok']
    when healthy; ok=False + findings list when corruption is detected.
    Advisory only; no writes are blocked.
    """
    from ..config import settings
    from ..db_integrity import deep_integrity_check

    ok, findings = await asyncio.to_thread(deep_integrity_check, settings.db_path)
    return {"ok": ok, "findings": findings}


@router.post("/admin/db/backup")
async def db_backup(request: Request) -> dict:
    """VACUUM INTO a timestamped clean copy of the database.

    VACUUM INTO writes all live pages to a new file atomically — safe
    while the database is open. The backup is a fully defragmented, valid
    SQLite file. Backups land in <db_path.parent>/backups/ (typically
    /data/backups/); the 5 most recent are kept, older ones are pruned.
    Returns path, size_bytes, created_at, and a list of pruned filenames.
    """
    from pathlib import Path

    from ..config import settings
    from ..db_integrity import vacuum_backup

    backups_dir = Path(settings.db_path).parent / "backups"
    try:
        result = await asyncio.to_thread(
            vacuum_backup,
            settings.db_path,
            backups_dir,
            when=time.time(),
        )
    except Exception as e:
        log.error("db backup failed: %s", e)
        raise HTTPException(500, detail=safe_error(e))
    return result
