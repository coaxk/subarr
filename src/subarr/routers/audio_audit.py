"""#155 phase 2 — library-wide audio-language audit API.

POST /api/audio-audit/start   kick the throttled background walker (409 if running)
POST /api/audio-audit/stop    cancel the walker
GET  /api/audio-audit         current progress + the actionable findings

The walker is OPT-IN (never auto-started) and GPU-polite (yields to live tuning-
lab sweeps), so starting it is safe even mid-sweep — it just trickles in the
background and pauses while sweeps run.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/audio-audit", tags=["audio-audit"])


@router.post("/start", status_code=202)
async def start_audit(request: Request) -> dict:
    walker = request.app.state.audio_audit
    if walker.is_running():
        raise HTTPException(409, detail="audio-language audit already running")
    state = await walker.start()
    return {"state": state.to_dict()}


@router.post("/stop")
async def stop_audit(request: Request) -> dict:
    walker = request.app.state.audio_audit
    await walker.stop()
    state = walker.get_state()
    return {"state": state.to_dict() if state is not None else None}


@router.get("")
async def get_audit(request: Request) -> dict:
    walker = request.app.state.audio_audit
    store = request.app.state.audio_audit_store
    state = walker.get_state()
    return {
        "state": state.to_dict() if state is not None else None,
        "findings": [f.to_dict() for f in store.list_findings()],
        "counts": store.count_by_status(),
    }
