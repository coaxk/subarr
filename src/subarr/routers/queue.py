"""GET /api/queue — subgen v4.2 queue + per-processing-task progress merge.

The /queue shape comes straight from subgen's v4.2 patch. We enrich each
`processing` row with a `progress` object pulled from the most recent
subgen log progress line that matches the file's basename.

Progress merging is best-effort: if docker.sock is unavailable, the
endpoint still returns the queue without progress (no error escalates).
Subgen's progress lines truncate the filename to ~38 chars + '..', so
we match by checking whether the bracket name is a prefix of
basename(processing.path) — exact-match would fail on long filenames.
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException, Request

from ..subgen_client import SubgenUnavailable

router = APIRouter(prefix="/api", tags=["queue"])
log = logging.getLogger(__name__)


def _match_progress(processing_path: str, progress_map: dict[str, dict]) -> dict | None:
    """Find a progress entry whose bracket-name is a prefix of the
    processing task's basename. Subgen left-truncates names ≥ ~38 chars
    by replacing the tail with '..', so we compare against the bare
    bracket text (already stripped of trailing dots in the regex)."""
    basename = os.path.basename(processing_path)
    if not basename or not progress_map:
        return None
    # Exact basename match first (short filenames).
    if basename in progress_map:
        return progress_map[basename]
    # Prefix match: bracket-name is the leading chars of basename.
    for name, prog in progress_map.items():
        if basename.startswith(name):
            return prog
    return None


@router.get("/queue")
async def get_queue(request: Request) -> dict:
    client = request.app.state.subgen
    docker_ops = request.app.state.docker
    try:
        q = await client.queue()
    except SubgenUnavailable as e:
        raise HTTPException(503, detail=str(e))

    progress_map = await docker_ops.recent_progress(tail=80)
    if progress_map and isinstance(q.get("processing"), list):
        for task in q["processing"]:
            prog = _match_progress(task.get("path", ""), progress_map)
            if prog is not None:
                task["progress"] = prog

    return q
