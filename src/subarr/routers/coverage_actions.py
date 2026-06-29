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


def _file_has_target_sub(fs_path, target_lang: str = "en") -> bool:
    """True when a `target_lang` .srt sidecar already sits next to the video —
    subgen would just skip the job ("Subtitles already exist in English"). A live
    per-file mirror of coverage's has_sub_on_disk, so we don't enqueue a job
    that's certain to no-op even if the (throttled) coverage snapshot is stale."""
    try:
        if not fs_path.is_file():
            return False
        from ..coverage_engine import _langs_in_sidecars

        stem = fs_path.stem
        srts = [
            p.name
            for p in fs_path.parent.iterdir()
            if p.is_file() and p.name.startswith(stem + ".") and p.suffix.lower() == ".srt"
        ]
        return target_lang in _langs_in_sidecars(srts)
    except OSError:
        return False


class CoverageQueueRequest(BaseModel):
    sonarr_episode_id: int | None = None
    # Fallback for movies / rows missing sonarr id.
    canonical_path: str | None = None
    # #368 follow-up: the owning movie's Radarr id (the row's bazarr_radarr_id),
    # carried so completion_watcher uploads the finished movie .srt straight to
    # Bazarr — the manual analogue of how the auto-queue already threads it.
    radarr_movie_id: int | None = None
    reverse: bool = False
    # #317 Slice B: "transcribe a full sub anyway" on a forced-only file —
    # forwards ?ignore_forced=true to subgen's /batch so it transcribes instead
    # of treating the forced-only embedded sub as "exists". The UI only sends
    # this when the connected subgen advertises capabilities.request_ignore_forced;
    # an older subgen silently drops the param (the file re-skips, no harm).
    ignore_forced: bool = False


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

    # #345/#351 follow-up: don't enqueue a file that already has a target-language
    # (English) sub on disk — subgen would just skip it, wasting a feed cycle and
    # cluttering the queue/Issues (and an interrupted skip looks like a "fail").
    # Live per-file check beats the throttled coverage snapshot, so a sub Bazarr
    # just wrote is caught now. The forced-only "transcribe anyway" path
    # (ignore_forced) bypasses this deliberately.
    if not req.ignore_forced and _file_has_target_sub(target):
        log.info("coverage_queue: %s already has an English sub on disk — not queued", canonical)
        return {
            "queued": False,
            "reason": "already_subtitled",
            "path": canonical,
            "detail": (
                "An English subtitle already exists on disk — not queued (subgen would skip it). "
                "If Bazarr still lists it as missing, it just hasn't re-indexed the file yet."
            ),
        }

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
        radarr_movie_id=req.radarr_movie_id,
        ignore_forced=req.ignore_forced,
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
