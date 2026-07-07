"""GET /api/logs/events — SSE stream of subgen container logs."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from ..docker_client import DockerUnavailable
from ..error_detail import safe_error

router = APIRouter(prefix="/api", tags=["logs"])


@router.get("/logs/events")
async def logs_events(request: Request, tail: int = Query(200, ge=0, le=5000)) -> StreamingResponse:
    docker_ops = request.app.state.docker

    async def gen():
        try:
            async for line in docker_ops.stream_subgen_logs(tail=tail):
                # Each line gets its own SSE event. JSON-encode to safely carry
                # any embedded newlines / quotes; the frontend parses the data.
                yield f"event: log\ndata: {json.dumps(line)}\n\n"
                if await request.is_disconnected():
                    return
        except DockerUnavailable as e:
            # #328: a dedicated event name — NOT "error", which collides with
            # the EventSource transport error and is ambiguous in the browser.
            # The frontend listens for this to render a "can't reach Docker"
            # panel with the socket-mount fix instead of spinning silently.
            yield f"event: stream_error\ndata: {json.dumps(safe_error(e))}\n\n"
        except asyncio.CancelledError:
            return

    return StreamingResponse(gen(), media_type="text/event-stream")


def _ring():
    """The process-wide LogRing, or None. Imported lazily so a missing/absent
    ring (early boot, tests that clear it) is tolerated, not a hard import error."""
    from .. import app as app_mod

    return getattr(app_mod, "LOG_RING", None)


@router.get("/logs/recent")
async def logs_recent(
    level: str | None = Query(None),
    limit: int = Query(200, ge=0, le=5000),
) -> dict:
    """#157 gap-fill: a snapshot of subarr's OWN recent log records, optionally
    filtered to `level` and above. Read-only; tolerates a missing ring."""
    ring = _ring()
    if ring is None:
        return {"records": []}
    return {"records": ring.snapshot(level=level, limit=limit)}


@router.get("/logs/subarr/events")
async def logs_subarr_events(request: Request, tail: int = Query(200, ge=0, le=5000)) -> StreamingResponse:
    """#157 gap-fill: live SSE tail of subarr's OWN log. Replays the last `tail`
    records then streams new ones, mirroring the subgen /logs/events shape."""
    ring = _ring()

    async def gen():
        if ring is None:
            # Nothing to stream (early boot / no ring). Close cleanly.
            return
        # Replay the tail as an initial burst.
        for rec in ring.snapshot(limit=tail):
            yield f"event: log\ndata: {json.dumps(rec)}\n\n"
        q = ring.subscribe()
        try:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    rec = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # Heartbeat comment keeps the connection alive through proxies.
                    yield ": keepalive\n\n"
                    continue
                yield f"event: log\ndata: {json.dumps(rec)}\n\n"
        except asyncio.CancelledError:
            return
        finally:
            ring.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream")
