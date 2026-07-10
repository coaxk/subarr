"""#364 slice 1 — forced-segment deep-scan walker control.

POST /api/forced-segment/start   kick the opt-in, GPU-polite walker (409 if running)
POST /api/forced-segment/stop    cancel it
GET  /api/forced-segment         progress + scan-cache summary
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/forced-segment", tags=["forced-segment"])

_SCOPES = ("coverage", "library")


@router.post("/start", status_code=202)
async def start_scan(request: Request, scope: str = "library") -> dict:
    # Validate scope BEFORE the running-check so a typo is a clear 400, not a
    # confusing 409. library = a full media-root deep scan (the opt-in default);
    # coverage = the tracked set.
    if scope not in _SCOPES:
        raise HTTPException(400, detail=f"scope must be one of {_SCOPES}")
    walker = getattr(request.app.state, "forced_segment", None)
    if walker is None:
        raise HTTPException(503, detail="forced-segment walker not available")
    if walker.is_running():
        raise HTTPException(409, detail="forced-segment scan already running")
    state = await walker.start(scope=scope)
    return {"state": state.to_dict(), "scope": scope}


@router.post("/stop")
async def stop_scan(request: Request) -> dict:
    walker = getattr(request.app.state, "forced_segment", None)
    if walker is None:
        return {"state": None}
    await walker.stop()
    state = walker.get_state()
    return {"state": state.to_dict() if state is not None else None}


@router.get("")
async def get_scan(request: Request) -> dict:
    walker = getattr(request.app.state, "forced_segment", None)
    store = getattr(request.app.state, "forced_segment_store", None)
    state = walker.get_state() if walker is not None else None
    return {
        "state": state.to_dict() if state is not None else None,
        "summary": store.summary() if store is not None else {},
    }
