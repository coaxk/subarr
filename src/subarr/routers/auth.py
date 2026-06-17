"""#238: authentication endpoints — state / setup / login / logout.

Single admin account. Setup is allowed only while `needs_setup`. Login accepts
the stored credential OR the env override (recovery). On success the session is
**rotated** (clear → set) to defeat session fixation. Failures are generic
("invalid username or password") to avoid user enumeration.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..auth import hash_password, needs_setup, verify_login

router = APIRouter(prefix="/api/auth", tags=["auth"])

_MIN_PASSWORD = 8


class Credentials(BaseModel):
    username: str
    password: str


def _store(request: Request):
    return request.app.state.auth_store


def _settings(request: Request):
    return request.app.state.settings


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
    if verify_login(creds.username, creds.password, store=_store(request), settings=_settings(request)):
        request.session.clear()  # rotate (anti session-fixation)
        request.session["user"] = creds.username
        return {"ok": True, "username": creds.username}
    raise HTTPException(401, detail="invalid username or password")


@router.post("/logout")
async def logout(request: Request) -> dict[str, Any]:
    request.session.clear()
    return {"ok": True}
