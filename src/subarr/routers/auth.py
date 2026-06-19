"""#238: authentication endpoints — state / setup / login / logout.

Single admin account. Setup is allowed only while `needs_setup`. Login accepts
the stored credential OR the env override (recovery). On success the session is
**rotated** (clear → set) to defeat session fixation. Failures are generic
("invalid username or password") to avoid user enumeration.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import hash_password, needs_setup, verify_login

router = APIRouter(prefix="/api/auth", tags=["auth"])

log = logging.getLogger(__name__)

_MIN_PASSWORD = 8
_MAX_PASSWORD = 1024


class Credentials(BaseModel):
    username: str
    password: str = Field(..., max_length=_MAX_PASSWORD)


def _store(request: Request):
    return request.app.state.auth_store


def _settings(request: Request):
    return request.app.state.settings


def _client_ip(request: Request) -> str:
    """Resolve the real client IP for throttling — honours X-Forwarded-For only
    from configured trusted proxies (see #260)."""
    from ..login_throttle import resolve_client_ip

    peer = request.client.host if request.client else ""
    xff = request.headers.get("x-forwarded-for", "")
    trusted = getattr(request.app.state, "trusted_proxies", [])
    return resolve_client_ip(peer, xff, trusted)


@router.get("/state")
async def state(request: Request) -> dict[str, Any]:
    user = request.session.get("user")
    # `user is not None` rather than bool(user): the session value is a non-empty
    # username or absent, and it avoids a bool()-typecast-on-input lint (semgrep
    # nan-injection) — harmless here (server-signed session) but cheap to dodge.
    return {
        "needs_setup": needs_setup(_settings(request), _store(request)),
        "authed": user is not None,
        "username": user,
    }


@router.post("/setup", status_code=201)
async def setup(creds: Credentials, request: Request) -> dict[str, Any]:
    store, settings = _store(request), _settings(request)
    client_ip = request.client.host if request.client else "unknown"
    log.warning(
        "first-run admin account setup invoked from %s -- if you did not initiate this, "
        "an attacker may have raced you; reset with SUBARR_AUTH_RESET",
        client_ip,
    )
    if not needs_setup(settings, store):
        raise HTTPException(409, detail="already configured")
    username = creds.username.strip()
    if not username:
        raise HTTPException(422, detail="username is required")
    if len(creds.password) < _MIN_PASSWORD:
        raise HTTPException(422, detail=f"password must be at least {_MIN_PASSWORD} characters")
    h, salt, iters = hash_password(creds.password)
    if not store.create_credential_if_absent(username, h, salt, iters):
        raise HTTPException(409, detail="already configured")  # lost a concurrent race
    request.session.clear()
    request.session["user"] = username
    return {"ok": True, "username": username}


@router.post("/login")
async def login(creds: Credentials, request: Request) -> dict[str, Any]:
    # #260: brute-force throttle, keyed on the resolved client IP. Setup is NOT
    # throttled — it creates the account (nothing to guess) and we don't want to
    # lock a first-run user out of their own setup.
    ip = _client_ip(request)
    throttle = getattr(request.app.state, "login_throttle", None)
    if throttle is not None:
        blocked, retry_after = throttle.check(ip, now=time.time())
        if blocked:
            raise HTTPException(
                429,
                detail="too many sign-in attempts; try again shortly",
                headers={"Retry-After": str(retry_after)},
            )
    if verify_login(creds.username, creds.password, store=_store(request), settings=_settings(request)):
        if throttle is not None:
            throttle.clear(ip)  # reset on success
        request.session.clear()  # rotate (anti session-fixation)
        request.session["user"] = creds.username
        return {"ok": True, "username": creds.username}
    if throttle is not None:
        throttle.record_failure(ip, now=time.time())
    raise HTTPException(401, detail="invalid username or password")


@router.post("/logout")
async def logout(request: Request) -> dict[str, Any]:
    request.session.clear()
    return {"ok": True}


@router.get("/throttle-config")
def throttle_config(request: Request) -> dict[str, Any]:
    """#260: effective brute-force-throttle config, read-only (set via env). No
    secrets — just the tunables + the parsed proxy/allowlist ranges for the
    Settings panel."""
    from ..login_throttle import parse_cidrs

    s = _settings(request)
    return {
        "max_attempts": s.login_max_attempts,
        "window_s": s.login_window_s,
        "trusted_proxies": [str(n) for n in parse_cidrs(s.trusted_proxies)],
        "allowlist": [str(n) for n in parse_cidrs(s.login_allowlist)],
    }
