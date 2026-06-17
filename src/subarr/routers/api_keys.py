"""#259: managed API key endpoints — list / create / revoke.

All routes sit BEHIND the auth gate (not in its bypass list), so only an
authenticated principal reaches them. A minted key authenticates as the admin
(full access), matching the single env SUBARR_API_KEY it sits alongside.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from ..api_keys import generate_key

router = APIRouter(prefix="/api/auth/keys", tags=["auth"])

_MAX_LABEL = 64


class KeyCreate(BaseModel):
    label: str


def _store(request: Request):
    return request.app.state.auth_store


@router.get("")
def list_keys(request: Request) -> dict[str, Any]:
    return {"keys": _store(request).list_api_keys()}


@router.post("", status_code=201)
def create_key(body: KeyCreate, request: Request) -> dict[str, Any]:
    label = body.label.strip()
    if not label:
        raise HTTPException(422, detail="label is required")
    if len(label) > _MAX_LABEL:
        raise HTTPException(422, detail=f"label must be at most {_MAX_LABEL} characters")
    token, token_hash, last4 = generate_key()
    key_id = _store(request).create_api_key(label, token_hash, last4)
    # `token` is the full plaintext — returned exactly once, never stored.
    return {"id": key_id, "label": label, "last4": last4, "token": token}


@router.delete("/{key_id}", status_code=204)
def delete_key(key_id: int, request: Request) -> Response:
    if not _store(request).delete_api_key(key_id):
        raise HTTPException(404, detail="key not found")
    return Response(status_code=204)
