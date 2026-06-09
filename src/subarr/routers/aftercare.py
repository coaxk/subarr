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
    # Best-effort enrich each row with the show's language from the in-memory
    # coverage snapshot (per-language is the tuning axis — drives the flag + the
    # #168 loop). No schema cost; read-time lookup against the cached snapshot.
    cc = getattr(request.app.state, "coverage_cache", None)
    snap = cc.get_cached() if cc is not None else None
    if snap is not None:
        from ..langs import normalize_lang

        lang_by_path: dict[str, str | None] = {}
        for it in snap.items:
            fp = it.get("file_canonical_path")
            if fp:
                raw = it.get("original_language") or next(
                    (a for a in (it.get("audio_langs") or []) if a), None
                )
                lang_by_path[fp] = normalize_lang(raw)
        for row in items:
            row["language"] = lang_by_path.get(row.get("canonical_path"))
    return {"count": len(items), "view": view, "items": items}


@router.post("/{result_id}/acknowledge")
async def acknowledge(result_id: int, request: Request) -> dict[str, Any]:
    store = request.app.state.aftercare
    if not store.mark_reviewed(result_id):
        raise HTTPException(404, detail=f"no pending aftercare result {result_id}")
    return {"ok": True, "id": result_id, "reviewed": True}
