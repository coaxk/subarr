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


def _verified_paths(request: Request) -> set[str]:
    """Canonical paths the user has already adjudicated (audio-lang verification
    = ground truth). Normalized to bare (no leading slash) so a finding stored
    with/without a leading slash still matches. Used to drop resolved files from
    the findings list — and to keep them dropped across a re-scan, which skips
    unchanged files by mtime and would otherwise leave the stale row forever."""
    store = getattr(request.app.state, "audio_lang", None)
    if store is None:
        return set()
    try:
        return {(p or "").lstrip("/") for p in store.get_all_as_lookup().keys()}
    except Exception:
        return set()


@router.get("")
async def get_audit(request: Request) -> dict:
    walker = request.app.state.audio_audit
    store = request.app.state.audio_audit_store
    state = walker.get_state()
    verified = _verified_paths(request)
    findings = [f.to_dict() for f in store.list_findings()
                if (f.canonical_path or "").lstrip("/") not in verified]
    return {
        "state": state.to_dict() if state is not None else None,
        "findings": findings,
        "counts": store.count_by_status(),
    }
