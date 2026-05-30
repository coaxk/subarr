"""POST /api/bazarr/sync-disk — tell Bazarr to rescan disk for a series.

When subarr's probe walk surfaces an English track Bazarr's wanted list
didn't account for, this endpoint nudges Bazarr to rescan and pick up
the embedded sub.

Bazarr's task names have shifted across releases. We try a hint list
(longest-prefix-match against task labels at runtime) and on failure
fall back to triggering the global scan task. If both fail, the caller
gets 502 with the available task list in the detail so the user can
report what's actually there.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..integrations import IntegrationError

router = APIRouter(prefix="/api", tags=["bazarr"])
log = logging.getLogger(__name__)


_TASK_HINTS = (
    # Bazarr 1.5.x canonical job_ids (verified against live API)
    "series_full_scan_subtitles",
    "movies_full_scan_subtitles",
    # Forward-compat
    "scan_disk_series",
    "scan_disk_episodes",
    # Human-readable labels (case-insensitive substring match)
    "index all existing episodes",
    "index all existing movies",
    "scan disk",
)


class SyncDiskRequest(BaseModel):
    series_id: int | None = None     # optional — when known, surfaced in response
    canonical_path: str | None = None  # optional — surfaced for context


@router.post("/bazarr/sync-disk")
async def sync_disk(request: Request, req: SyncDiskRequest | None = None) -> dict:
    # Accept empty body (button-click case) — pydantic model is optional now.
    req = req or SyncDiskRequest()
    bundle = request.app.state.integrations
    if not bundle.bazarr.is_configured():
        raise HTTPException(503, detail="bazarr not configured")

    try:
        tasks = await bundle.bazarr.list_tasks()
    except IntegrationError as e:
        raise HTTPException(503, detail=f"bazarr list_tasks failed: {e}")

    task_id = _find_task_id(tasks)
    if task_id is None:
        sample = [t.get("name") for t in tasks][:20]
        raise HTTPException(
            502,
            detail=(
                f"no Bazarr task matched scan-disk hints {list(_TASK_HINTS)!r}. "
                f"Available task names: {sample}"
            ),
        )

    try:
        await bundle.bazarr.trigger_task(task_id)
    except IntegrationError as e:
        raise HTTPException(502, detail=f"bazarr trigger_task({task_id}) failed: {e}")

    return {
        "triggered": True,
        "task_id": task_id,
        "series_id": req.series_id,
        "canonical_path": req.canonical_path,
    }


def _find_task_id(tasks: list[dict]) -> str | None:
    """Match task labels (case-insensitive) against the hint list.
    Returns the first non-empty job_id / id / name found."""
    for hint in _TASK_HINTS:
        h = hint.lower()
        for t in tasks:
            label = (t.get("name") or t.get("job_id") or "").lower()
            if h in label:
                return t.get("job_id") or t.get("id") or t.get("name")
    return None
