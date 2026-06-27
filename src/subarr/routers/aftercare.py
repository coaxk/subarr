"""#156 Track A: aftercare review endpoints.

GET  /api/aftercare/pending             - flagged & unreviewed count (header pill)
GET  /api/aftercare/results?view=&source=&... - latest-per-path results (page)
POST /api/aftercare/audit               - #216 start the existing-sub audit
GET  /api/aftercare/audit/status        - #216 audit progress
POST /api/aftercare/{id}/acknowledge    - mark reviewed
POST /api/aftercare/{id}/regenerate     - #216 regenerate an EXTERNAL sub from audio

Requeue of GENERATED subs is NOT here - the frontend reuses POST
/api/queue/requeue (which already resolves audio_language_override) then calls
acknowledge. DRY. Regenerate (above) is the existing-audit variant: it resolves
the .srt's sibling VIDEO before queueing, since subgen transcribes from media.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from ..audio_lang_store import resolve_audio_language_override
from ..existing_audit import EXISTING_AUDIT_SOURCE, resolve_media_for_srt
from ..paths import PathOutsideRootError, fs_to_canonical

router = APIRouter(prefix="/api/aftercare", tags=["aftercare"])
log = logging.getLogger(__name__)


@router.get("/pending")
async def pending(request: Request) -> dict[str, Any]:
    store = request.app.state.aftercare
    return {"count": store.pending_count()}


@router.post("/audit")
async def start_audit(request: Request) -> dict[str, Any]:
    """#216: kick off the library-wide existing-subtitle audit. Single-flight —
    `started=false` means one is already running (not an error)."""
    svc = request.app.state.existing_audit
    started = svc.start()
    return {"started": started, "status": svc.status()}


@router.get("/audit/status")
async def audit_status(request: Request) -> dict[str, Any]:
    """#216: progress for the existing-sub audit (running, done/total, summary)."""
    return request.app.state.existing_audit.status()


@router.get("/results")
async def results(
    request: Request,
    view: str = Query("flagged", pattern="^(flagged|all)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    source: str | None = Query(None, max_length=40),
) -> dict[str, Any]:
    store = request.app.state.aftercare
    items = store.list_results(view=view, limit=limit, offset=offset, source=source)
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
    # #378: library provenance label (independent of the coverage snapshot —
    # resolved straight from each row's canonical path; fail-soft to library 0).
    from ..paths import library_label

    for row in items:
        row["library"] = library_label(row.get("canonical_path") or "")
    return {"count": len(items), "view": view, "items": items}


@router.post("/acknowledge-all")
async def acknowledge_all(
    request: Request, source: str | None = Query(None, max_length=40)
) -> dict[str, Any]:
    """#313: bulk-acknowledge every still-pending result in one action — clears
    a large first-run backlog (e.g. a freshly-audited library) without per-row
    clicks. Optional source filter matches the /results view ('existing_audit')."""
    store = request.app.state.aftercare
    n = store.mark_all_reviewed(source=source)
    return {"ok": True, "acknowledged": n}


@router.post("/{result_id}/acknowledge")
async def acknowledge(result_id: int, request: Request) -> dict[str, Any]:
    store = request.app.state.aftercare
    if not store.mark_reviewed(result_id):
        raise HTTPException(404, detail=f"no pending aftercare result {result_id}")
    return {"ok": True, "id": result_id, "reviewed": True}


@router.post("/{result_id}/regenerate", status_code=202)
async def regenerate(result_id: int, request: Request) -> dict[str, Any]:
    """#216 P4: regenerate-from-audio for an existing-audit (external) sub.

    The row's canonical_path is the .srt; subgen transcribes from the VIDEO, so
    we resolve the sibling media, map it to a canonical, and enqueue THAT — never
    the .srt. Generated rows use the normal /api/queue/requeue path instead.
    """
    store = request.app.state.aftercare
    row = store.get(result_id)
    if row is None:
        raise HTTPException(404, detail=f"no aftercare result {result_id}")
    if row.get("source") != EXISTING_AUDIT_SOURCE:
        raise HTTPException(
            400, detail="regenerate is for existing-audit subs; use /queue/requeue for generated ones"
        )
    media = resolve_media_for_srt(row["canonical_path"])
    if media is None:
        raise HTTPException(422, detail="no sibling video found to regenerate from")
    try:
        canonical = fs_to_canonical(media)
    except PathOutsideRootError:
        raise HTTPException(400, detail="media file is outside the media root")
    audio_language_override = resolve_audio_language_override(
        getattr(request.app.state, "audio_lang", None), canonical, caller="regenerate", log=log
    )
    pending = request.app.state.pending_queue
    job = pending.enqueue(canonical, source="manual", audio_language_override=audio_language_override)
    request.app.state.queue_feeder.kick()
    return {"job": job.id, "path": canonical, "status": "pending"}
