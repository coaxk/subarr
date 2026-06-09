"""POST /api/scan, GET /api/scan/{id}, GET /api/scan/{id}/events (SSE)."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..paths import PathOutsideRootError, canonical_to_fs
from ..provenance import SOURCE_SUBGENSCAN

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
        if not target.exists():
            raise HTTPException(404, detail=f"not found: {raw!r}")
        # Both directories and individual files are valid scan targets —
        # subgen's v4.1 transcribe_existing handles os.path.isfile(path) too.
        if not (target.is_dir() or target.is_file()):
            raise HTTPException(400, detail=f"not a file or directory: {raw!r}")
        cleaned.append(p)

    store = request.app.state.scans
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

    scan = store.create(cleaned, reverse=req.reverse)
    runner.start(scan)

    # Provenance: record each submitted path so /api/provenance can find it.
    # series_id is unknown here (Scan-tab picks don't carry one) — the
    # completion watcher will still mark completed_at, but won't trigger
    # Bazarr scan-disk for these. That's correct: manual Scan-tab picks are
    # power-user surgery; Coverage-tab queues are the Bazarr-aware path.
    provenance = request.app.state.provenance
    for p in cleaned:
        provenance.record(
            canonical_path=p,
            scan_id=scan.id,
            source=SOURCE_SUBGENSCAN,
        )
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
