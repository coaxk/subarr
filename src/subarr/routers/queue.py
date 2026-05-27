"""GET /api/queue — proxy to subgen's /queue (v4.2 patched endpoint).

We deliberately do NOT reshape the response. Subarr's frontend reads the same
shape subgen produces; if subgen's shape changes, the GUI version-pins against
subgen and updates together. Less divergence than translating.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..subgen_client import SubgenUnavailable

router = APIRouter(prefix="/api", tags=["queue"])


@router.get("/queue")
async def get_queue(request: Request) -> dict:
    client = request.app.state.subgen
    try:
        return await client.queue()
    except SubgenUnavailable as e:
        raise HTTPException(503, detail=str(e))
