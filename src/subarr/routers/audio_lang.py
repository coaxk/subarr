"""v1.1-O Layer 4: Manual audio-language verification endpoints.

GET  /api/audio-lang/verifications        — list all stored verifications
GET  /api/audio-lang/verifications/{path} — get one
POST /api/audio-lang/verifications        — upsert (user confirms language)
DELETE /api/audio-lang/verifications/{path} — remove a verification
POST /api/audio-lang/verifications/bulk-for-series — apply to multiple files
GET  /api/audio-lang/pending-review       — coverage rows needing user input
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..audio_sampler import (
    extract_sample, find_dialog_positions,
)
from ..config import settings
from ..paths import PathOutsideRootError, canonical_to_fs

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/audio-lang", tags=["audio-lang"])


class VerifyRequest(BaseModel):
    canonical_path: str
    lang_code: str
    source: str = "user"
    confidence: float = 1.0
    evidence: dict | None = None


@router.get("/verifications")
async def list_verifications(request: Request) -> dict[str, Any]:
    store = request.app.state.audio_lang
    rows = [v.to_dict() for v in store.list_all()]
    return {"count": len(rows), "verifications": rows}


@router.post("/verifications")
async def upsert_verification(req: VerifyRequest, request: Request) -> dict[str, Any]:
    store = request.app.state.audio_lang
    store.upsert(
        canonical_path=req.canonical_path,
        lang_code=req.lang_code,
        source=req.source,
        confidence=req.confidence,
        evidence=req.evidence,
    )
    # v1.1 ARCH fix #197: kick a background coverage refresh so the
    # corresponding row's chip turns green within a few seconds, not 30.
    import asyncio as _asyncio
    cov_cache = getattr(request.app.state, "coverage_cache", None)
    if cov_cache is not None and not cov_cache.is_refreshing():
        bundle = request.app.state.integrations
        probe_store = request.app.state.probe_store

        async def _refresh():
            try:
                await cov_cache.refresh(bundle, probe_store, store)
            except Exception as e:
                log.warning("post-verify refresh failed: %s", e)
        _asyncio.create_task(_refresh())
    return {"verified": True, "canonical_path": req.canonical_path,
            "lang_code": req.lang_code.lower()}


@router.delete("/verifications/{canonical_path:path}")
async def delete_verification(canonical_path: str, request: Request) -> dict[str, Any]:
    store = request.app.state.audio_lang
    removed = store.delete(canonical_path)
    if not removed:
        raise HTTPException(404, detail="not verified")
    return {"deleted": True, "canonical_path": canonical_path}


class BulkSeriesRequest(BaseModel):
    series_canonical_prefix: str   # e.g. "TV/Flics"
    lang_code: str
    file_paths: list[str]


@router.post("/verifications/bulk-for-series")
async def bulk_for_series(req: BulkSeriesRequest, request: Request) -> dict[str, Any]:
    store = request.app.state.audio_lang
    n = store.bulk_for_series(
        series_canonical_prefix=req.series_canonical_prefix,
        lang_code=req.lang_code,
        file_paths=req.file_paths,
    )
    return {"upserted": n, "lang_code": req.lang_code.lower()}


def _resolve_canonical_to_fs(canonical: str) -> str:
    """Translate a canonical path (TV/Show/...) into an absolute fs path
    on subarr's container view, with traversal guard."""
    try:
        target = canonical_to_fs(canonical)
    except (PathOutsideRootError, ValueError) as e:
        raise HTTPException(400, detail=f"invalid path: {e}")
    if not target.is_file():
        raise HTTPException(404, detail=f"not found: {canonical}")
    return str(target)


@router.get("/sample-positions")
async def sample_positions(
    canonical_path: str = Query(..., description="canonical path under media_root"),
    track: int = Query(0, ge=0, description="audio stream index"),
    n: int = Query(3, ge=1, le=6),
) -> dict[str, Any]:
    """v1.1-O Layer 4++: scan the file for non-silent regions and return
    N suggested sample start positions. UI uses these to populate the
    audio-review player's 'Sample 1 / 2 / 3' buttons — guarantees the user
    isn't hearing dead air on first play."""
    fs = _resolve_canonical_to_fs(canonical_path)
    result = await find_dialog_positions(fs, track=track, n=n)
    return {
        "canonical_path": canonical_path,
        "track": track,
        "duration_s": result.duration_s,
        "audio_tracks": result.audio_tracks,
        "positions": result.positions,
        "silence_count": len(result.silence_ranges),
    }


@router.get("/sample")
async def sample(
    canonical_path: str = Query(...),
    start: float = Query(0.0, ge=0.0),
    duration: float = Query(5.0, gt=0.0, le=30.0),
    track: int = Query(0, ge=0),
):
    """v1.1-O Layer 4++: stream a short MP3 of the requested audio
    window. Browser plays inline via <audio> element."""
    fs = _resolve_canonical_to_fs(canonical_path)

    async def _gen():
        async for chunk in extract_sample(fs, start_s=start, duration_s=duration, track=track):
            yield chunk

    return StreamingResponse(
        _gen(),
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": "inline",
        },
    )


@router.get("/pending-review")
async def pending_review(request: Request) -> dict[str, Any]:
    """v1.1-O Layer 4: surface coverage rows that need user audio-lang
    verification — suspect or unknown flags set, no existing verification.

    v1.1 ARCH: reads from the coverage_cache snapshot instead of rebuilding
    coverage end-to-end. Returns instantly (was 60-90s)."""
    audio_lang_store = request.app.state.audio_lang
    verifications = audio_lang_store.get_all_as_lookup()
    cov_cache = getattr(request.app.state, "coverage_cache", None)
    items_source: list[dict[str, Any]] = []
    if cov_cache is not None:
        snap = cov_cache.get_cached()
        if snap is not None:
            items_source = snap.items
    if not items_source:
        # No cache yet (very fresh boot) — fall back to a quick build.
        from ..coverage_engine import build_coverage
        bundle = request.app.state.integrations
        probe_store = request.app.state.probe_store
        report = await build_coverage(
            bundle, use_tautulli=True, probe_store=probe_store,
            audio_lang_store=audio_lang_store,
        )
        items_source = report.to_dict()["items"]
    pending = []
    for it in items_source:
        file_path = it.get("file_canonical_path")
        # Skip if already verified
        if file_path and file_path in verifications:
            continue
        flag = None
        if it.get("audio_label_suspect"):
            flag = "suspect"
        elif it.get("audio_label_unknown"):
            flag = "unknown"
        if not flag:
            continue
        pending.append({
            "canonical_path": it.get("canonical_path"),
            "file_canonical_path": file_path,
            "title": it.get("title"),
            "episode_number": it.get("episode_number"),
            "original_language": it.get("original_language"),
            "audio_langs": it.get("audio_langs"),
            "flag": flag,
            "notes": it.get("audio_label_notes"),
        })
    return {"count": len(pending), "items": pending[:200]}
