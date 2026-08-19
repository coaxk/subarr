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
    # #169: kept for API compatibility but no longer meaningful — submissions
    # now route through the pending queue one file at a time, so per-scan
    # processing order is governed by queue priority/position, not this flag.
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
        if not target.exists():
            raise HTTPException(404, detail=f"not found: {raw!r}")
        # Both directories and individual files are valid scan targets —
        # subgen's v4.1 transcribe_existing handles os.path.isfile(path) too.
        if not (target.is_dir() or target.is_file()):
            raise HTTPException(400, detail=f"not a file or directory: {raw!r}")
        cleaned.append(p)

    runner = request.app.state.runner

    # Compat-mode gate: refuse the scan upfront if subgen lacks /batch.
    # 503 carries a structured `reason` so the UI can surface a clear
    # "needs subarr-subgen" notice instead of generic "scan failed".
    try:
        runner._check_can_scan()
    except Exception as e:
        from .. import scan_runner as _sr

        if isinstance(e, _sr.CompatModeError):
            raise HTTPException(
                503,
                detail={
                    "error": "compat_mode",
                    "reason": str(e),
                    "remedy": "Switch SUBGEN_URL to ghcr.io/coaxk/subarr-subgen, "
                    "or see Settings → Integrations → subgen for the "
                    "current compat-mode status.",
                },
            )
        raise

    # #169: route through the pending queue instead of submitting immediately,
    # so manual submits are governed by the same advanced queue (visible,
    # throttled, reorderable) as everything else instead of stampeding subgen.
    # Manual is the top priority bucket → the feeder feeds it first; kick() wakes
    # the feeder now instead of waiting out its interval. The feeder owns
    # scan-row creation + provenance at submit time (series_id stays unknown for
    # Scan-tab picks — power-user surgery, not the Bazarr-aware path). enqueue()
    # dedups against anything already pending/in-flight for the same path.
    pending = request.app.state.pending_queue
    jobs = [
        pending.enqueue(p, source="manual", submission_origin="manual_scan")  # #451
        for p in cleaned
    ]
    request.app.state.queue_feeder.kick()
    return {
        "enqueued": [j.canonical_path for j in jobs],
        "jobs": [j.id for j in jobs],
        "count": len(jobs),
        "status": "pending",
    }


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
