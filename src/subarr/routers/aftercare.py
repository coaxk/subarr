"""#156 Track A: aftercare review endpoints.

GET  /api/aftercare/pending             - flagged & unreviewed count (header pill)
GET  /api/aftercare/results?view=&...   - latest-per-path results (page)
POST /api/aftercare/{id}/acknowledge    - mark reviewed

Requeue is NOT here - the frontend reuses POST /api/queue/requeue (which already
resolves audio_language_override) then calls acknowledge. DRY.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(prefix="/api/aftercare", tags=["aftercare"])


@router.get("/pending")
async def pending(request: Request) -> dict[str, Any]:
    store = request.app.state.aftercare
    return {"count": store.pending_count()}


@router.get("/results")
async def results(
    request: Request,
    view: str = Query("flagged", pattern="^(flagged|all)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    store = request.app.state.aftercare
    items = store.list_results(view=view, limit=limit, offset=offset)
    return {"count": len(items), "view": view, "items": items}


@router.post("/{result_id}/acknowledge")
async def acknowledge(result_id: int, request: Request) -> dict[str, Any]:
    store = request.app.state.aftercare
    if not store.mark_reviewed(result_id):
        raise HTTPException(404, detail=f"no pending aftercare result {result_id}")
    return {"ok": True, "id": result_id, "reviewed": True}
