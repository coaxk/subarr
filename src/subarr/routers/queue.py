"""GET /api/queue — Featured Queue: unified view across live subgen state +
subarr's scan-store history. Plus /requeue, /delete, /clear-history actions.

Before v1.1.1 this endpoint just proxied subgen's /queue, which meant any
submission that subgen accepted (HTTP 200) but skipped (audio language in
SKIP_IF_AUDIO_LANGUAGES, target subs already exist, etc.) vanished into
the void — the UI showed "submitted" then nothing in queue. Silent fails.

Now we merge:
- LIVE from subgen: processing + queued (as today)
- HISTORY from subarr scan_store: every submission in the last `history_window_s`
  seconds, with per-path status + reason (skipped / error / ok / done).
The frontend renders sections: Processing · Queued · Issues · Recently done.

Per-row actions:
- POST /api/queue/requeue            — resubmit a path (canonical) to subgen
- DELETE /api/queue/scan/{scan_id}   — drop one scan from history
- POST /api/queue/clear              — bulk-purge by status (done/error/skipped)
"""
from __future__ import annotations

import logging
import os
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..paths import PathOutsideRootError, canonical_to_fs
from ..provenance import SOURCE_SUBGENSCAN
from ..scan_store import (
    PATH_STATUS_ERROR,
    PATH_STATUS_OK,
    PATH_STATUS_RUNNING,
    PATH_STATUS_SKIPPED,
    SCAN_STATUS_DONE,
    SCAN_STATUS_ERROR,
)
from ..subgen_client import SubgenUnavailable

router = APIRouter(prefix="/api", tags=["queue"])
log = logging.getLogger(__name__)

# How far back to look in scan_store when building the history view.
# 24h captures a full day's submissions without dragging in noise.
_DEFAULT_HISTORY_WINDOW_S = 24 * 3600


def _match_progress(processing_path: str, progress_map: dict[str, dict]) -> dict | None:
    """Find a progress entry whose bracket-name is a prefix of the
    processing task's basename. Subgen left-truncates names ≥ ~38 chars
    by replacing the tail with '..', so we compare against the bare
    bracket text (already stripped of trailing dots in the regex)."""
    basename = os.path.basename(processing_path)
    if not basename or not progress_map:
        return None
    if basename in progress_map:
        return progress_map[basename]
    for name, prog in progress_map.items():
        if basename.startswith(name):
            return prog
    return None


def _path_outcome_chip(status: str, body: dict | None, error: str | None) -> dict:
    """Derive a single 'outcome' shape per scan path for the UI:
    {category: 'ok'|'skipped'|'error'|'running'|'pending', label, detail}.
    The frontend picks chip colour from category."""
    if status == PATH_STATUS_RUNNING:
        return {"category": "running", "label": "running",
                "detail": "scan_runner forwarding to subgen"}
    if status == PATH_STATUS_OK:
        queued = (body or {}).get("queued", 0) if isinstance(body, dict) else 0
        return {"category": "ok", "label": "queued",
                "detail": f"subgen queued {queued}" if queued else "subgen accepted"}
    if status == PATH_STATUS_SKIPPED:
        return {"category": "skipped", "label": "skipped",
                "detail": error or "subgen walked but queued nothing"}
    if status == PATH_STATUS_ERROR:
        return {"category": "error", "label": "failed",
                "detail": error or "submission errored"}
    return {"category": "pending", "label": status, "detail": ""}


@router.get("/queue")
async def get_queue(request: Request, history_window_s: int = _DEFAULT_HISTORY_WINDOW_S) -> dict:
    """Featured Queue: live subgen + recent subarr scan history merged.

    Response shape:
    {
      "processing": [...subgen processing with progress merged...],
      "queued":     [...subgen queued...],
      "processing_count": int,
      "queued_count":     int,
      # New in v1.1.1:
      "history": [
        {
          "scan_id": str, "created_at": float, "scan_status": str,
          "path": str, "outcome": {category,label,detail},
          "started_at": float|null, "finished_at": float|null,
          "subgen_status_code": int|null,
        },
        ...
      ],
      "history_counts": {"ok": int, "skipped": int, "error": int, "running": int},
    }
    """
    client = request.app.state.subgen
    docker_ops = request.app.state.docker

    # LIVE (subgen). Soft-fail: if subgen is down, history is still useful.
    live: dict = {"processing": [], "queued": [],
                  "processing_count": 0, "queued_count": 0}
    try:
        q = await client.queue()
        live.update(q)
    except SubgenUnavailable as e:
        log.warning("subgen unreachable; serving history-only queue view: %s", e)
        live["subgen_error"] = str(e)

    progress_map = await docker_ops.recent_progress(tail=80)
    if progress_map and isinstance(live.get("processing"), list):
        for task in live["processing"]:
            prog = _match_progress(task.get("path", ""), progress_map)
            if prog is not None:
                task["progress"] = prog

    # HISTORY (subarr scan_store). Flatten scans → per-path rows so each
    # row in the UI is a single submission outcome, not a scan-with-N-paths.
    store = request.app.state.scans
    since = time.time() - history_window_s
    scans = store.list_recent(since_epoch=since, limit=500)
    history: list[dict] = []
    counts = {"ok": 0, "skipped": 0, "error": 0, "running": 0}
    # Build a set of paths currently live in subgen so we don't double-count
    # an in-flight row as "running" via scan_store AND "processing" via subgen.
    live_paths: set[str] = set()
    for t in live.get("processing", []) or []:
        if isinstance(t, dict) and t.get("path"):
            live_paths.add(os.path.basename(t["path"]))
    for t in live.get("queued", []) or []:
        if isinstance(t, dict) and t.get("path"):
            live_paths.add(os.path.basename(t["path"]))
    for scan in scans:
        for r in scan.results:
            basename = os.path.basename(r.path)
            # Skip history row if this path is currently live in subgen —
            # it'll be visible in the processing/queued section already.
            if basename in live_paths and r.status == PATH_STATUS_RUNNING:
                continue
            outcome = _path_outcome_chip(r.status, r.subgen_body, r.error)
            cat = outcome["category"]
            if cat in counts:
                counts[cat] += 1
            history.append({
                "scan_id": scan.id,
                "created_at": scan.created_at,
                "scan_status": scan.status,
                "path": r.path,
                "outcome": outcome,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
                "subgen_status_code": r.subgen_status_code,
            })

    return {
        **live,
        "history": history,
        "history_counts": counts,
        "history_window_s": history_window_s,
    }


# ─── Per-row actions ─────────────────────────────────────────────────────

class RequeueRequest(BaseModel):
    # Canonical path under media_root. We don't requeue by scan_id because
    # a scan can have multiple paths; the user picks one row at a time.
    path: str
    reverse: bool = False


# #58: subgen-side cancel. Proxies to subarr-subgen v4.4 POST /queue/cancel.
class CancelRequest(BaseModel):
    path: str


@router.post("/queue/cancel")
async def cancel_queued(req: CancelRequest, request: Request) -> dict:
    """Cancel a queued (NOT processing) task in subgen. Routes through
    the v4.4 capability — if the live subgen doesn't advertise
    queue_cancel, return 503 so the UI can show 'upgrade subgen to v4.4'.

    Response body mirrors what subgen returns:
      { cancelled: bool, reason?: str, path: str }
    """
    canonical = (req.path or "").strip().strip("/")
    if not canonical:
        raise HTTPException(400, detail="path required")
    caps = getattr(request.app.state, "subgen_caps", None)
    if caps is None or not getattr(caps, "queue_cancel", False):
        raise HTTPException(
            503,
            detail=(
                "queue_cancel capability missing — upgrade subarr-subgen to v4.4+. "
                "Current subgen rev: " + (getattr(caps, "subarr_subgen_patch_rev", None) or "vanilla")
            ),
        )
    subgen = request.app.state.subgen
    try:
        result = await subgen.queue_cancel(canonical)
    except SubgenUnavailable as e:
        raise HTTPException(502, detail=f"subgen unavailable: {e}")
    return result


@router.post("/queue/requeue", status_code=202)
async def requeue(req: RequeueRequest, request: Request) -> dict:
    """Resubmit a single path to subgen via the scan runner. Creates a
    fresh scan_store row so the new attempt appears in history with its
    own outcome — original row is untouched (audit trail preserved)."""
    canonical = (req.path or "").strip().strip("/")
    if not canonical:
        raise HTTPException(400, detail="path required")
    try:
        target = canonical_to_fs(canonical)
    except PathOutsideRootError:
        raise HTTPException(400, detail=f"path escapes media root: {canonical!r}")
    if not target.exists():
        raise HTTPException(404, detail=f"not found on disk: {canonical!r}")
    store = request.app.state.scans
    runner = request.app.state.runner
    scan = store.create([canonical], reverse=req.reverse)
    runner.start(scan)
    provenance = request.app.state.provenance
    provenance.record(
        canonical_path=canonical, scan_id=scan.id, source=SOURCE_SUBGENSCAN,
    )
    return {"id": scan.id, "path": canonical, "status": scan.status}


@router.delete("/queue/scan/{scan_id}")
async def delete_scan(scan_id: str, request: Request) -> dict:
    """Drop one scan from history. Doesn't touch the live subgen queue —
    that's controlled by subgen itself (subarr-subgen v4.3 task #58 wires
    pause/cancel for live jobs). This is purely scan_store cleanup."""
    store = request.app.state.scans
    if not store.delete(scan_id):
        raise HTTPException(404, detail=f"scan {scan_id} not found")
    return {"deleted": True, "scan_id": scan_id}


class ClearRequest(BaseModel):
    # Which categories to purge from history. Maps to scan_store SCAN_STATUS_*.
    # Valid: "done" | "error". "skipped" alone isn't a scan-level status
    # (skipped is per-path); a scan with only skipped paths ends up
    # SCAN_STATUS_DONE with all results skipped, so "done" handles that.
    statuses: list[str]
    older_than_s: int | None = None


@router.post("/queue/clear")
async def clear_history(req: ClearRequest, request: Request) -> dict:
    """Bulk-purge scan history. Default categories the UI offers: done +
    error. Optional `older_than_s` lets the user say e.g. 'clear everything
    older than 1 day' (default: no age cutoff — purges all matching)."""
    valid = {SCAN_STATUS_DONE, SCAN_STATUS_ERROR}
    bad = [s for s in req.statuses if s not in valid]
    if bad:
        raise HTTPException(400, detail=f"invalid statuses: {bad}; "
                                        f"allowed: {sorted(valid)}")
    store = request.app.state.scans
    cutoff = (time.time() - req.older_than_s) if req.older_than_s else None
    n = store.delete_where_status_in(req.statuses, older_than=cutoff)
    return {"deleted": n, "statuses": req.statuses, "older_than_s": req.older_than_s}
