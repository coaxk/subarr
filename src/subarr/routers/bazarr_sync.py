"""POST /api/bazarr/sync-disk — tell Bazarr to rescan disk for a series and/or movies.

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
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..integrations import IntegrationError
from ..integrations.bazarr import MOVIES_SCAN_TASK_HINTS, SERIES_SCAN_TASK_HINTS, scan_task_id
from ..paths import library_for_canonical

router = APIRouter(prefix="/api", tags=["bazarr"])
log = logging.getLogger(__name__)


class SyncDiskRequest(BaseModel):
    series_id: int | None = None  # optional — when known, surfaced in response
    canonical_path: str | None = None  # optional — surfaced for context
    media_type: Literal["episode", "movie"] | None = None  # None = both kinds


@router.post("/bazarr/sync-disk")
async def sync_disk(request: Request, req: SyncDiskRequest | None = None) -> dict:
    # Accept empty body (button-click case) — pydantic model is optional now.
    req = req or SyncDiskRequest()
    bundle = request.app.state.integrations
    # #161 P3: trigger the scan on the Bazarr that owns this row's library
    # (instance 0 when no canonical_path is supplied — the bare button click).
    bazarr = (
        bundle.client_for("bazarr", library_for_canonical(req.canonical_path).bazarr_id)
        if req.canonical_path
        else bundle.bazarr
    )
    if not bazarr.is_configured():
        raise HTTPException(503, detail="bazarr not configured")

    try:
        tasks = await bazarr.list_tasks()
    except IntegrationError as e:
        raise HTTPException(503, detail=f"bazarr list_tasks failed: {e}")

    if req.media_type is None:
        kinds = ["series", "movies"]
    elif req.media_type == "episode":
        kinds = ["series"]
    else:
        kinds = ["movies"]

    task_ids: list[str] = []
    for kind in kinds:
        tid = scan_task_id(tasks, kind)
        if tid is not None and tid not in task_ids:
            task_ids.append(tid)

    if not task_ids:
        hints = [
            hint
            for kind in kinds
            for hint in (SERIES_SCAN_TASK_HINTS if kind == "series" else MOVIES_SCAN_TASK_HINTS)
        ]
        sample = [t.get("name") for t in tasks if isinstance(t, dict)][:20]
        raise HTTPException(
            502,
            detail=(f"no Bazarr task matched scan-disk hints {hints!r}. Available task names: {sample}"),
        )

    triggered: list[str] = []
    for task_id in task_ids:
        try:
            await bazarr.trigger_task(task_id)
        except IntegrationError as e:
            raise HTTPException(502, detail=f"bazarr trigger_task({task_id}) failed: {e}")
        triggered.append(task_id)

    return {
        "triggered": True,
        "task_id": triggered[0],
        "task_ids": triggered,
        "series_id": req.series_id,
        "canonical_path": req.canonical_path,
    }
