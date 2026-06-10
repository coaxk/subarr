"""POST /api/coverage/queue — resolve a Bazarr-wanted row to a single
file path and enqueue it via the existing scan runner.

Bazarr's wanted payload gives us `sonarrEpisodeId` but no file path. We
resolve Sonarr → episode → episode_file → path, then strip the
ARR_PATH_PREFIX to canonical form. That way we queue ONE .mkv file
rather than the whole series directory.

Movies fall back to the series/movie directory because Radarr's
identifiers in Bazarr's wanted payload don't expose a per-file id
the same way Sonarr's do (the wanted row has the movie itself, which
IS the single video file). For movies the canonical path is already
file-level, so no resolution needed.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..integrations import IntegrationError
from ..paths import PathOutsideRootError, canonical_to_fs, strip_arr_prefix
from ..audio_lang_store import resolve_audio_language_override

router = APIRouter(prefix="/api", tags=["coverage"])
log = logging.getLogger(__name__)


class CoverageQueueRequest(BaseModel):
    sonarr_episode_id: int | None = None
    # Fallback for movies / rows missing sonarr id.
    canonical_path: str | None = None
    reverse: bool = False


@router.post("/coverage/queue", status_code=202)
async def coverage_queue(req: CoverageQueueRequest, request: Request) -> dict:
    bundle = request.app.state.integrations

    canonical: str | None = None
    resolved_via: str = ""
    series_id: int | None = None

    if req.sonarr_episode_id is not None:
        if not bundle.sonarr.is_configured():
            raise HTTPException(503, detail="sonarr not configured; cannot resolve episode id")
        try:
            ep = await bundle.sonarr.episode(req.sonarr_episode_id)
        except IntegrationError as e:
            raise HTTPException(502, detail=f"sonarr episode lookup failed: {e}")

        series_id = ep.get("seriesId")
        ep_file_id = ep.get("episodeFileId")
        if not ep_file_id:
            raise HTTPException(
                404,
                detail=(
                    f"sonarr episode {req.sonarr_episode_id} has no episodeFileId "
                    "(file not present on disk according to Sonarr)"
                ),
            )
        try:
            ep_file = await bundle.sonarr.episode_file(ep_file_id)
        except IntegrationError as e:
            raise HTTPException(502, detail=f"sonarr episode_file lookup failed: {e}")

        arr_path = ep_file.get("path")
        if not arr_path:
            raise HTTPException(500, detail=f"sonarr episode_file {ep_file_id} has no path field")
        canonical = strip_arr_prefix(arr_path)
        resolved_via = f"sonarr_episode_id={req.sonarr_episode_id}"

    elif req.canonical_path:
        canonical = req.canonical_path.strip().strip("/")
        resolved_via = "canonical_path"
    else:
        raise HTTPException(400, detail="must provide sonarr_episode_id or canonical_path")

    # Validate the resolved path exists on disk before enqueueing.
    try:
        target = canonical_to_fs(canonical)
    except PathOutsideRootError:
        raise HTTPException(400, detail=f"resolved path escapes media root: {canonical!r}")
    if not target.exists():
        raise HTTPException(
            404,
            detail=(
                f"resolved path not present on subarr's media mount: {canonical!r}. "
                "Sonarr's filesystem view and subarr's may have diverged."
            ),
        )
    if not (target.is_file() or target.is_dir()):
        raise HTTPException(400, detail=f"resolved path is neither file nor dir: {canonical!r}")

    # #229: shared override-resolution helper. Same logic now used by
    # the requeue endpoint — see audio_lang_store.resolve_audio_language_
    # override for the evidence gate (#105) and the risky-script logging.
    audio_language_override = resolve_audio_language_override(
        getattr(request.app.state, "audio_lang", None),
        canonical,
        caller="coverage_queue",
        log=log,
    )

    # #66/#116 slice 6: route through the pending queue (throttled), instead of
    # flooding subgen directly. The feeder drains it to subgen at target depth
    # and writes provenance then — so series_id/sonarr_episode_id are carried on
    # the pending row for completion_watcher's Bazarr trigger. Dedup is built
    # into enqueue() (an already-pending/submitted path returns the same job).
    pending = request.app.state.pending_queue
    job = pending.enqueue(
        canonical,
        source="gaps",
        audio_language_override=audio_language_override,
        series_id=series_id,
        sonarr_episode_id=req.sonarr_episode_id,
    )

    return {
        "id": job.id,
        "pending_id": job.id,
        "queued_to": "pending",
        "canonical_path": canonical,
        "resolved_via": resolved_via,
        "status": job.status,
        "is_file": target.is_file(),
        "series_id": series_id,
    }
