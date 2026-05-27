"""POST /api/scan, GET /api/scan/{id}, GET /api/scan/{id}/events (SSE)."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..paths import PathOutsideRootError, canonical_to_fs

router = APIRouter(prefix="/api", tags=["scan"])


class ScanRequest(BaseModel):
    paths: list[str] = Field(..., min_length=1)
    reverse: bool = False


@router.post("/scan", status_code=202)
async def create_scan(req: ScanRequest, request: Request) -> dict:
    cleaned: list[str] = []
    for raw in req.paths:
        p = raw.strip().strip("/")
        if not p:
            raise HTTPException(400, detail="empty path in request")
        try:
            target = canonical_to_fs(p)
        except PathOutsideRootError:
            raise HTTPException(400, detail=f"path escapes media root: {raw!r}")
        if not target.exists() or not target.is_dir():
            raise HTTPException(404, detail=f"not a directory: {raw!r}")
        cleaned.append(p)

    store = request.app.state.scans
    runner = request.app.state.runner
    scan = store.create(cleaned, reverse=req.reverse)
    runner.start(scan)
    return {"id": scan.id, "paths": scan.paths, "status": scan.status, "reverse": scan.reverse}


@router.get("/scan/{scan_id}")
async def get_scan(scan_id: str, request: Request) -> dict:
    scan = request.app.state.scans.get(scan_id)
    if scan is None:
        raise HTTPException(404, detail="scan not found")
    return scan.to_dict()


@router.get("/scan/{scan_id}/events")
async def scan_events(scan_id: str, request: Request) -> StreamingResponse:
    scan = request.app.state.scans.get(scan_id)
    if scan is None:
        raise HTTPException(404, detail="scan not found")
    runner = request.app.state.runner

    async def gen():
        try:
            async for evt in runner.subscribe(scan_id):
                payload = json.dumps(evt.get("data"))
                yield f"event: {evt['event']}\ndata: {payload}\n\n"
        except asyncio.CancelledError:
            return

    return StreamingResponse(gen(), media_type="text/event-stream")
