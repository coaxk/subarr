"""GET /api/logs/events — SSE stream of subgen container logs."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..docker_client import DockerUnavailable

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
            yield f"event: error\ndata: {json.dumps(str(e))}\n\n"
        except asyncio.CancelledError:
            return

    return StreamingResponse(gen(), media_type="text/event-stream")
