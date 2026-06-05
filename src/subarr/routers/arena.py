"""#131 — tuning-lab arena API.

POST /api/arena/run         start a config sweep (background; returns the run)
GET  /api/arena/runs        list runs
GET  /api/arena/{id}        run state (poll while running, or just read once)
GET  /api/arena/{id}/events SSE stream of live progress

The sweep runs against the live subgen model over the v4.10 /asr path-input
channel (no upload, no shared scratch). Gated on capabilities.asr_arena so
older/vanilla subgen gets a clear "needs subarr-subgen >=v4.10" instead of a
confusing failure.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..arena import ConfigVariant
from ..paths import PathOutsideRootError, canonical_to_fs

router = APIRouter(prefix="/api/arena", tags=["arena"])


class VariantSpec(BaseModel):
    label: str = Field(..., min_length=1)
    kwargs: dict = Field(default_factory=dict)


class ArenaRunRequest(BaseModel):
    media_path: str = Field(..., min_length=1)
    variants: list[VariantSpec] = Field(..., min_length=1)
    source_language: str | None = None


@router.post("/run", status_code=202)
async def create_arena_run(req: ArenaRunRequest, request: Request) -> dict:
    p = req.media_path.strip().strip("/")
    if not p:
        raise HTTPException(400, detail="empty media_path")
    try:
        target = canonical_to_fs(p)
    except PathOutsideRootError:
        raise HTTPException(400, detail=f"path escapes media root: {req.media_path!r}")
    if not target.is_file():
        raise HTTPException(404, detail=f"not a file: {req.media_path!r}")

    labels = [v.label for v in req.variants]
    if len(set(labels)) != len(labels):
        raise HTTPException(400, detail="variant labels must be unique")

    # Capability gate upfront: a clear 503 beats kicking off a run that will
    # fail at preflight. asr_arena = subarr-subgen >=v4.10 (/asr path+kwargs).
    caps = getattr(request.app.state, "subgen_caps", None)
    if not getattr(caps, "asr_arena", False):
        raise HTTPException(
            503,
            detail={
                "error": "unsupported",
                "reason": "the tuning lab needs subarr-subgen >=v4.10 — its /asr "
                          "endpoint must advertise the arena channel (path-input + "
                          "per-request kwargs).",
                "remedy": "Upgrade your subgen image to "
                          "ghcr.io/coaxk/subarr-subgen:latest (>=2026.05.3-r4).",
            },
        )

    svc = request.app.state.arena
    variants = [ConfigVariant(v.label, v.kwargs) for v in req.variants]
    run = svc.create(p, variants, source_language=req.source_language)
    svc.start(run)
    return run.to_dict()


@router.get("/runs")
async def list_arena_runs(request: Request) -> dict:
    # Lightweight summaries (no scorecards), newest-first — the sweeps list.
    # Full detail (ranked table) is fetched per-run via GET /api/arena/{id}.
    return {"runs": [r.summary() for r in request.app.state.arena.list()]}


@router.get("/by-language")
async def arena_by_language(request: Request) -> dict:
    # [#26] herd view: per-language recipe stats aggregated across completed
    # sweeps. Defined BEFORE /{run_id} so it isn't captured as a run id.
    return {"languages": request.app.state.arena.aggregate_by_language()}


@router.get("/{run_id}")
async def get_arena_run(run_id: str, request: Request) -> dict:
    run = request.app.state.arena.get(run_id)
    if run is None:
        raise HTTPException(404, detail="arena run not found")
    return run.to_dict()


@router.delete("/{run_id}", status_code=204)
async def delete_arena_run(run_id: str, request: Request):
    if not request.app.state.arena.delete(run_id):
        raise HTTPException(404, detail="arena run not found")
    return None


@router.get("/{run_id}/events")
async def arena_events(run_id: str, request: Request) -> StreamingResponse:
    svc = request.app.state.arena
    if svc.get(run_id) is None:
        raise HTTPException(404, detail="arena run not found")

    async def gen():
        try:
            async for evt in svc.subscribe(run_id):
                payload = json.dumps(evt.get("data"))
                yield f"event: {evt['event']}\ndata: {payload}\n\n"
        except asyncio.CancelledError:
            return

    return StreamingResponse(gen(), media_type="text/event-stream")
