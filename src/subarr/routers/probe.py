"""ffprobe HTTP API: GET /api/probe, POST /api/probe/walk, walk SSE."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..media_probe import ProbeError, probe as run_probe
from ..paths import PathOutsideRootError, canonical_to_fs

router = APIRouter(prefix="/api", tags=["probe"])
log = logging.getLogger(__name__)


class WalkRequest(BaseModel):
    path: str
    recursive: bool = True  # currently always recursive; kept for future


@router.get("/probe")
async def probe_endpoint(request: Request, path: str = Query("")) -> dict:
    canonical = path.strip().strip("/")
    if not canonical:
        raise HTTPException(400, detail="path is required")
    try:
        target = canonical_to_fs(canonical)
    except PathOutsideRootError:
        raise HTTPException(400, detail=f"path escapes media root: {canonical!r}")
    if not target.exists():
        raise HTTPException(404, detail=f"not found: {canonical!r}")
    if not target.is_file():
        raise HTTPException(400, detail=f"not a file: {canonical!r}")

    store = request.app.state.probe_store
    try:
        st = target.stat()
    except OSError as e:
        raise HTTPException(500, detail=f"stat failed: {e}")

    cached = store.get(canonical, mtime=st.st_mtime, size=st.st_size)
    if cached is not None:
        return cached.to_dict()

    try:
        result = await run_probe(target)
    except ProbeError as e:
        raise HTTPException(503, detail=str(e))
    result.canonical_path = canonical
    store.upsert(canonical_path=canonical, mtime=st.st_mtime, size=st.st_size, result=result)
    return result.to_dict()


@router.post("/probe/walk")
async def start_walk(req: WalkRequest, request: Request) -> dict:
    canonical = req.path.strip().strip("/")
    try:
        target = canonical_to_fs(canonical)
    except PathOutsideRootError:
        raise HTTPException(400, detail=f"path escapes media root: {canonical!r}")
    if not target.exists() or not target.is_dir():
        raise HTTPException(404, detail=f"not a directory: {canonical!r}")
    walker = request.app.state.probe_walker
    state = await walker.start_walk(canonical)
    return state.to_dict()


@router.get("/probe/walk/{walk_id}")
async def get_walk(walk_id: str, request: Request) -> dict:
    walker = request.app.state.probe_walker
    state = walker.get(walk_id)
    if state is None:
        raise HTTPException(404, detail="walk not found")
    return state.to_dict()


@router.get("/probe/walks")
async def list_walks(request: Request) -> dict:
    walker = request.app.state.probe_walker
    return {"walks": [s.to_dict() for s in walker.list_all()]}


@router.get("/probe/library")
async def library(
    request: Request,
    filter_text: str = Query("", description="Substring filter on canonical path"),
    only_with_eng: bool = Query(False, description="Only rows whose probe found an English sub"),
    only_without_eng: bool = Query(False, description="Only rows with NO English sub"),
    sub_kind: str = Query("", description="Filter by classification: full|sdh|forced|commentary|none"),
    limit: int = Query(500, ge=1, le=5000),
) -> dict:
    """Library Probe tab data: every cached probe with classification.

    Independent of Bazarr's wanted list — shows what subarr's probe sees
    on disk. Used to confirm 'this file has X embedded' and feed back to
    Coverage's hide_embedded_en filter."""
    from ..media_probe import (
        audio_lang_summary,
        english_track_summary,
        has_forced_or_commentary_english,
        has_usable_embedded_english,
    )

    store = request.app.state.probe_store
    rows = store.all_entries()
    out: list[dict] = []
    f = filter_text.lower().strip()
    kind_filter = sub_kind.lower().strip()

    for r in rows:
        if f and f not in r.canonical_path.lower():
            continue
        usable = has_usable_embedded_english(r)
        partial = has_forced_or_commentary_english(r)
        en_summary = english_track_summary(r)
        if only_with_eng and not (usable or partial):
            continue
        if only_without_eng and (usable or partial):
            continue
        if kind_filter:
            row_kind = "none"
            if en_summary == "EN":
                row_kind = "full"
            elif en_summary == "EN(SDH)":
                row_kind = "sdh"
            elif en_summary == "EN(forced)":
                row_kind = "forced"
            elif en_summary == "EN(commentary)":
                row_kind = "commentary"
            if row_kind != kind_filter:
                continue
        out.append(
            {
                "canonical_path": r.canonical_path,
                "duration_s": r.duration_s,
                "audio_langs": audio_lang_summary(r),
                "english_track": en_summary,
                "usable_english": usable,
                "subtitle_streams": [
                    {
                        "language": s.language,
                        "title": s.title,
                        "codec": s.codec,
                        "forced": s.forced,
                        "sdh": s.sdh,
                        "commentary": s.commentary,
                    }
                    for s in r.subtitles
                ],
                "probed_at": r.probed_at,
            }
        )
        if len(out) >= limit:
            break

    return {
        "total_cached": len(rows),
        "shown": len(out),
        "items": out,
    }


@router.get("/probe/walk/{walk_id}/events")
async def walk_events(walk_id: str, request: Request) -> StreamingResponse:
    walker = request.app.state.probe_walker
    if walker.get(walk_id) is None:
        raise HTTPException(404, detail="walk not found")

    async def gen():
        try:
            async for evt in walker.subscribe(walk_id):
                payload = json.dumps(evt.get("data"))
                yield f"event: {evt['event']}\ndata: {payload}\n\n"
        except asyncio.CancelledError:
            return

    return StreamingResponse(gen(), media_type="text/event-stream")
