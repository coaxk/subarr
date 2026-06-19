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

from ..audio_lang_store import resolve_audio_language_override
from ..config import settings
from ..log_safe import scrub
from ..paths import PathOutsideRootError, canonical_to_fs
from ..scan_store import (
    PATH_STATUS_ERROR,
    PATH_STATUS_OK,
    PATH_STATUS_ORPHANED,
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

# Subtitle sidecar extensions subgen treats as an already-existing subtitle.
# Mirrors has_external_subtitle_in_language() in subgen_patched.py — a file
# subgen skipped because ANY of these sits next to it (not only .srt) was
# previously mislabeled 'unknown' (#89). Kept in sync with subgen's set.
_SUBTITLE_SIDECAR_EXTS = (
    ".srt",
    ".vtt",
    ".sub",
    ".ass",
    ".ssa",
    ".idx",
    ".sbv",
    ".pgs",
    ".ttml",
    ".lrc",
)


def match_progress(processing_path: str, progress_map: dict[str, dict]) -> dict | None:
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


def _infer_skip_reason(canonical_path: str) -> str:
    """Best-effort disambiguation of subgen's single 'skipped' counter.

    subgen /batch returns one integer for 'skipped' regardless of which
    should_skip_file branch fired (target sub exists, SKIP_IF_AUDIO_LANGUAGES,
    internal sub language match, lrc exists, etc.). The per-file reason
    only appears in subgen's logs. Until subgen emits a per-reason
    breakdown (subgen-side patch — out of scope here), we infer the most
    common case heuristically: if a recognized subtitle sidecar sits next
    to the video file the user submitted, classify as 'sub_exists'.
    Otherwise 'unknown' (most often audio_lang, but other branches are
    possible).

    The sidecar match covers every extension subgen's
    has_external_subtitle_in_language() recognizes (see
    _SUBTITLE_SIDECAR_EXTS), not just .srt — a skip caused by an existing
    .ass/.vtt/etc. sidecar is just as much "sub already exists" and should
    not land in the noisy 'unknown' bucket (#89).

    NOTE: subgen ALSO skips on EMBEDDED (internal) subtitles and on
    SKIP_IF_AUDIO_LANGUAGES. Detecting either requires opening/probing the
    media container — a signal this function does not have (the scan_store
    PathResult it's derived from carries only path/status/error, no
    audio_langs or embedded-sub flags). Those remain 'unknown' here; see
    the follow-up note in the PR for wiring probe data through.

    Returns 'sub_exists' | 'audio_lang' | 'unknown'. 'audio_lang' is
    reserved for a future subgen-side patch; today this helper never
    returns it.
    """
    try:
        fs_path = canonical_to_fs(canonical_path)
    except (PathOutsideRootError, OSError):
        return "unknown"
    # canonical_path may point at a directory (season folder) or a single
    # file. For a directory we can't cheaply pick THE file subgen tripped
    # on, so we conservatively report 'unknown'.
    if not fs_path.is_file():
        return "unknown"
    parent = fs_path.parent
    stem = fs_path.stem
    if not parent.is_dir():
        return "unknown"
    try:
        for sibling in parent.iterdir():
            if not sibling.is_file():
                continue
            name = sibling.name
            # Match `<stem>.<ext>`, `<stem>.<lang>.<ext>`,
            # `<stem>.<lang>.<flag>.<ext>` for any subtitle sidecar ext.
            if name.startswith(stem + ".") and name.lower().endswith(_SUBTITLE_SIDECAR_EXTS):
                return "sub_exists"
    except OSError:
        return "unknown"
    return "unknown"


def _path_outcome_chip(
    status: str,
    body: dict | None,
    error: str | None,
    canonical_path: str | None = None,
) -> dict:
    """Derive a single 'outcome' shape per scan path for the UI:
    {category, label, detail, skip_reason?}.

    skip_reason is set only when category == 'skipped' and is one of
    'sub_exists' | 'audio_lang' | 'unknown'. The frontend uses it to
    pick chip colour (info-gray for sub_exists — that's not really an
    issue) vs. warn-amber (unknown — the user may want to verify their
    audio-lang skip list).
    """
    if status == PATH_STATUS_RUNNING:
        return {"category": "running", "label": "running", "detail": "scan_runner forwarding to subgen"}
    if status == PATH_STATUS_OK:
        queued = (body or {}).get("queued", 0) if isinstance(body, dict) else 0
        return {
            "category": "ok",
            "label": "queued",
            "detail": f"subgen queued {queued}" if queued else "subgen accepted",
        }
    if status == PATH_STATUS_SKIPPED:
        skip_reason = _infer_skip_reason(canonical_path) if canonical_path else "unknown"
        if skip_reason == "sub_exists":
            label = "sub already exists"
            detail = "matching .srt already on disk — subgen had nothing to do. Not an issue."
        else:
            label = "skipped"
            detail = (
                error
                or "subgen walked but queued nothing — likely "
                "audio-language match. Check your skip-language "
                "list if this surprised you."
            )
        return {"category": "skipped", "label": label, "detail": detail, "skip_reason": skip_reason}
    if status == PATH_STATUS_ERROR:
        return {"category": "error", "label": "failed", "detail": error or "submission errored"}
    if status == PATH_STATUS_ORPHANED:
        # #229 phase 2: subgen restarted between subarr's accept and the
        # .srt landing on disk. Surface in its own category so the Queue
        # UI can route to a "Lost on restart" bucket and show a prominent
        # requeue button rather than burying these in Recently-done.
        return {
            "category": "orphaned",
            "label": "lost on restart",
            "detail": error or "subgen restarted before transcription completed",
        }
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
    live: dict = {"processing": [], "queued": [], "processing_count": 0, "queued_count": 0}
    try:
        q = await client.queue()
        live.update(q)
    except SubgenUnavailable as e:
        log.warning("subgen unreachable; serving history-only queue view: %s", e)
        live["subgen_error"] = str(e)

    # Tuning-lab arena sweeps run through subgen via /asr UPLOAD mode, so they
    # surface in subgen's /queue with a subgen-side temp upload path (not a
    # `<subgen_media_prefix>/…` library path). They belong to the Tuning Lab,
    # not the main queue — without this filter the sidebar counted a sweep
    # ("1 running") that the queue page never rendered (it keys off library
    # paths), so the count and the list disagreed. Drop non-library jobs from
    # BOTH the lists and the counts so they stay in sync.
    _media_prefix = settings.subgen_media_prefix.rstrip("/") + "/"

    def _is_library_job(t: object) -> bool:
        return isinstance(t, dict) and isinstance(t.get("path"), str) and t["path"].startswith(_media_prefix)

    live["processing"] = [t for t in (live.get("processing") or []) if _is_library_job(t)]
    live["queued"] = [t for t in (live.get("queued") or []) if _is_library_job(t)]
    live["processing_count"] = len(live["processing"])
    live["queued_count"] = len(live["queued"])

    progress_map = await docker_ops.recent_progress(tail=80)
    if progress_map and isinstance(live.get("processing"), list):
        for task in live["processing"]:
            prog = match_progress(task.get("path", ""), progress_map)
            if prog is not None:
                task["progress"] = prog

    # HISTORY (subarr scan_store). Flatten scans → per-path rows so each
    # row in the UI is a single submission outcome, not a scan-with-N-paths.
    store = request.app.state.scans
    since = time.time() - history_window_s
    scans = store.list_recent(since_epoch=since, limit=500)
    history: list[dict] = []
    counts = {"ok": 0, "skipped": 0, "error": 0, "running": 0, "orphaned": 0}
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
            # it's already shown in the processing/queued section. This
            # covers BOTH a running scan_store row AND a just-submitted OK
            # row ("subgen queued 1"): without OK here, a freshly-queued
            # file showed in the live Queued section AND again in
            # Recently-done. Skipped/error/orphaned rows are kept so real
            # outcomes still surface even if a newer submission is live.
            if basename in live_paths and r.status in (PATH_STATUS_RUNNING, PATH_STATUS_OK):
                continue
            outcome = _path_outcome_chip(
                r.status,
                r.subgen_body,
                r.error,
                canonical_path=r.path,
            )
            cat = outcome["category"]
            if cat in counts:
                counts[cat] += 1
            history.append(
                {
                    "scan_id": scan.id,
                    "created_at": scan.created_at,
                    "scan_status": scan.status,
                    "path": r.path,
                    "outcome": outcome,
                    "started_at": r.started_at,
                    "finished_at": r.finished_at,
                    "subgen_status_code": r.subgen_status_code,
                }
            )

    # Count of arena sweeps actively transcribing (running /asr) — NOT sweeps
    # parked in waiting_for_capacity (those hold no GPU slot, so the "using a
    # slot" indicator stays silent until they actually start). A waiting sweep is
    # surfaced on its own Tuning-Lab card, not here.
    arena = getattr(request.app.state, "arena", None)
    arena_active = arena.inflight_count() if arena is not None else 0

    return {
        **live,
        "history": history,
        "history_counts": counts,
        "history_window_s": history_window_s,
        "arena_active": arena_active,
    }


# ─── subgen push completion (#87) ────────────────────────────────────────


class SubgenCompletedWebhook(BaseModel):
    """Payload subgen POSTs to WEBHOOK_URL_COMPLETED on task finish.

    Verified against subgen_patched.send_completion_webhook (2026-06):
      { "event": "transcribed"|"translated"|...,
        "file":     "/media/TV/Show/file.mkv",   # subgen-space abs path
        "subtitle": "/media/TV/Show/file.en.srt",
        "language": "en" }

    `file` is the only field we strictly need (it keys the provenance
    ledger after prefix-stripping). Everything else is accepted + logged
    so a future subgen field never 422s the hook. extra="allow" keeps
    unknown keys visible for logging without breaking validation.
    """

    model_config = {"extra": "allow"}

    file: str | None = None
    subtitle: str | None = None
    event: str | None = None
    language: str | None = None


@router.post("/subgen/webhook/completed")
async def subgen_webhook_completed(
    payload: SubgenCompletedWebhook,
    request: Request,
) -> dict:
    """Push-based completion receiver (#87) — the low-latency alternative
    to polling subgen's /queue. Operators point subgen's
    WEBHOOK_URL_COMPLETED at this endpoint; subgen POSTs here the instant a
    transcribe/translate finishes, and we run the SAME completion flow the
    polling watcher uses (mark provenance completed → Bazarr write-back →
    Plex partial-scan). Polling stays running as the fallback for vanilla
    subgen setups that haven't configured the webhook.

    Reuses CompletionWatcher.complete_by_canonical so the battle-tested
    Bazarr/Plex logic lives in exactly one place. Idempotent: if polling
    already completed the entry, this is a benign no-op (matched=0).
    """
    from ..config import settings as _settings

    if not _settings.subgen_webhook_enabled:
        # Operator opted out of push; tell subgen we received it so it
        # doesn't log a delivery failure, but do nothing (polling drives).
        return {"accepted": False, "reason": "webhook disabled", "matched": 0}

    if not payload.file:
        raise HTTPException(400, detail="payload missing 'file'")

    # Log any fields beyond the documented shape so a subgen change is
    # discoverable rather than silently dropped.
    known = {"file", "subtitle", "event", "language"}
    extra = {k: v for k, v in payload.model_dump().items() if k not in known}
    if extra:
        log.info("subgen completion webhook carried unknown fields: %s", extra)

    from ..paths import subgen_to_canonical

    canonical = subgen_to_canonical(payload.file)
    watcher = request.app.state.watcher
    matched = await watcher.complete_by_canonical(canonical)
    log.info(
        "subgen completion webhook: event=%s file=%s canonical=%s matched=%d",
        scrub(payload.event),  # webhook fields are untrusted (no auth by default)
        scrub(payload.file),
        scrub(canonical),
        matched,
    )
    return {
        "accepted": True,
        "canonical_path": canonical,
        "event": payload.event,
        "matched": matched,
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
    # The path here is a SUBGEN queue key — it lives in subgen's path space
    # (e.g. /media/...), which is NOT the same mount as subarr's media root
    # (/media/library/...). It comes straight from GET /api/queue, and
    # subgen matches its queue by EXACT path. So pass it through verbatim:
    # stripping the leading slash or canonicalising it against subarr's root
    # (the previous behaviour) made every cancel miss with "not in queue".
    # subgen only does an in-memory queue lookup here, so this never touches
    # the filesystem; a basic traversal guard is the only check that applies.
    canonical = (req.path or "").strip()
    if not canonical:
        raise HTTPException(400, detail="path required")
    if ".." in canonical:
        raise HTTPException(400, detail=f"invalid path: {canonical!r}")
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
    """Resubmit a single path through the pending queue (#169). Routes via the
    same advanced queue as everything else (manual priority → fed first), rather
    than submitting immediately. The feeder creates the scan_store history row +
    records provenance at submit time, so the new attempt still appears in
    history with its own outcome and the original row is untouched."""
    canonical = (req.path or "").strip().strip("/")
    if not canonical:
        raise HTTPException(400, detail="path required")
    try:
        target = canonical_to_fs(canonical)
    except PathOutsideRootError:
        raise HTTPException(400, detail=f"path escapes media root: {canonical!r}")
    if not target.exists():
        raise HTTPException(404, detail=f"not found on disk: {canonical!r}")
    # #229: pass the audio_language_override if the user has verified
    # this file's audio language. Previously requeue lacked this lookup
    # so subgen would silently skip any file whose audio tag matched
    # SKIP_IF_AUDIO_LANGUAGES — the requeue click "succeeded" (200 OK,
    # green chip) but no transcription ever happened. The user had no
    # working manual recovery path. Shared helper with coverage_queue.
    audio_language_override = resolve_audio_language_override(
        getattr(request.app.state, "audio_lang", None),
        canonical,
        caller="requeue",
        log=log,
    )
    # #169: route through the pending queue like manual submit. enqueue carries
    # the #229 audio_language_override on the row; the feeder applies it at
    # submit time and records provenance. Manual priority + kick() = near-
    # immediate. enqueue dedups if the path is already pending/in-flight.
    pending = request.app.state.pending_queue
    job = pending.enqueue(canonical, source="manual", audio_language_override=audio_language_override)
    request.app.state.queue_feeder.kick()
    return {"job": job.id, "path": canonical, "status": "pending"}


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
        raise HTTPException(400, detail=f"invalid statuses: {bad}; allowed: {sorted(valid)}")
    store = request.app.state.scans
    cutoff = (time.time() - req.older_than_s) if req.older_than_s else None
    n = store.delete_where_status_in(req.statuses, older_than=cutoff)
    return {"deleted": n, "statuses": req.statuses, "older_than_s": req.older_than_s}


# ─── #66/#116: pending-queue authority (mutation + control) ──────────
#
# The pending queue is subarr's own backlog in front of subgen (pending_queue
# store + feeder). Pending rows are fully ours → reorder/promote/demote/remove.
# Once a row is 'submitted' it lives in subgen's queue → mutation is rejected
# (409); cancel it via /queue/cancel like any subgen item. pause/target_depth
# live on the auto-queue rules so the feeder reads them live (no restart).
from ..pending_queue import STATUS_PENDING  # noqa: E402


class PendingMoveRequest(BaseModel):
    before_id: str | None = None
    after_id: str | None = None


class QueueControlRequest(BaseModel):
    paused: bool | None = None
    target_depth: int | None = None


@router.get("/queue/pending")
async def list_pending(request: Request) -> dict:
    """The pending backlog (reorderable) + what subarr currently has submitted
    to subgen, plus the live feed controls."""
    store = request.app.state.pending_queue
    rules = request.app.state.schedule.get_rules()
    return {
        "pending": [j.to_dict() for j in store.list(status=STATUS_PENDING)],
        "submitted": [j.to_dict() for j in store.list(status="submitted")],
        "counts": store.count_by_status(),
        "paused": rules.queue_paused,
        "target_depth": rules.queue_target_depth,
    }


@router.post("/queue/control")
async def set_queue_control(req: QueueControlRequest, request: Request) -> dict:
    """Toggle the feed (pause/resume) and/or set the target depth. Persisted on
    the auto-queue rules; the feeder picks it up on its next tick."""
    schedule = request.app.state.schedule
    rules = schedule.get_rules()
    if req.paused is not None:
        rules.queue_paused = bool(req.paused)
    if req.target_depth is not None:
        rules.queue_target_depth = max(0, int(req.target_depth))
    schedule.set_rules(rules)
    return {"paused": rules.queue_paused, "target_depth": rules.queue_target_depth}


def _require_pending(store, job_id: str):
    """Fetch a job and ensure it's still pending (not yet handed to subgen).
    Submitted rows can't be reordered — they're subgen's now (409)."""
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, detail=f"pending job {job_id!r} not found")
    if job.status != STATUS_PENDING:
        raise HTTPException(
            409,
            detail=(
                f"job {job_id!r} is {job.status} — already handed to subgen; "
                "reorder only applies to pending jobs (cancel it instead)."
            ),
        )
    return job


@router.post("/queue/pending/{job_id}/move")
async def move_pending(job_id: str, req: PendingMoveRequest, request: Request) -> dict:
    store = request.app.state.pending_queue
    _require_pending(store, job_id)
    if not (req.before_id or req.after_id):
        raise HTTPException(400, detail="provide before_id or after_id")
    try:
        return store.move(job_id, before_id=req.before_id, after_id=req.after_id).to_dict()
    except KeyError as e:
        raise HTTPException(404, detail=str(e))


@router.post("/queue/pending/{job_id}/promote")
async def promote_pending(job_id: str, request: Request) -> dict:
    store = request.app.state.pending_queue
    _require_pending(store, job_id)
    return store.promote(job_id).to_dict()


@router.post("/queue/pending/{job_id}/demote")
async def demote_pending(job_id: str, request: Request) -> dict:
    store = request.app.state.pending_queue
    _require_pending(store, job_id)
    return store.demote(job_id).to_dict()


@router.delete("/queue/pending/{job_id}")
async def remove_pending(job_id: str, request: Request) -> dict:
    store = request.app.state.pending_queue
    if not store.remove(job_id):
        raise HTTPException(404, detail=f"pending job {job_id!r} not found")
    return {"deleted": True, "id": job_id}


@router.post("/queue/backfill")
async def backfill_library(request: Request) -> dict:
    """#116: load the whole verified-gap backlog into the pending queue as
    low-priority backfill. The feeder then drains it gently at target_depth, so
    a 500-gap library closes in the background instead of stampeding the GPU.
    Honours the same auto-queue quality filters; dedups against what's already
    pending/in-flight (so re-running just tops up). Reads the cached coverage
    snapshot — run a coverage walk first if it's cold."""
    from ..backfill import eligible_backfill_items
    from ..coverage_engine import CoverageItem
    from .schedule import _COVERAGE_ITEM_FIELDS

    cov = getattr(request.app.state, "coverage_cache", None)
    snap = cov.get_cached() if cov is not None else None
    if snap is None:
        raise HTTPException(409, detail="coverage cache not warm yet — run a coverage walk first")

    items = [CoverageItem(**{k: v for k, v in d.items() if k in _COVERAGE_ITEM_FIELDS}) for d in snap.items]
    rules = request.app.state.schedule.get_rules()
    pending = request.app.state.pending_queue
    prov = request.app.state.provenance
    in_flight = {e.canonical_path for e in prov.pending()} | pending.active_paths()

    eligible = eligible_backfill_items(items, rules, in_flight_paths=in_flight)
    enqueued = 0
    for it in eligible:
        canonical = it.file_canonical_path or it.canonical_path
        if not canonical:
            continue
        pending.enqueue(canonical, source="backfill", sonarr_episode_id=it.bazarr_episode_id)
        enqueued += 1

    counts = pending.count_by_status()
    return {
        "enqueued": enqueued,
        "pending_total": counts.get(STATUS_PENDING, 0),
        "snapshot_age_s": round(time.time() - snap.generated_at, 1),
    }
