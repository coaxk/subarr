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
from ..paths import PathOutsideRootError, canonical_to_fs
from ..provenance import SOURCE_SUBGENSCAN
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


# subgen logs a `Skipping <basename>: <reason>` line per skipped file.
# Map each known reason phrase to the skip_reason enum value the UI
# uses. Phrases come from subgen_patched.py should_skip_file() — keep
# this table in sync if subgen adds a new branch. Matched case-insensitive
# on substring so log-prefix variations (timestamps, log levels) don't
# break the classifier.
_SKIP_REASON_PHRASES: tuple[tuple[str, str], ...] = (
    # SKIP_IF_TARGET_SUBTITLES_EXIST + sibling-extension matches
    ("subtitles already exist", "sub_exists"),
    ("generated subtitle", "sub_exists"),
    ("lrc file already exists", "sub_exists"),
    # SKIP_IF_AUDIO_LANGUAGES
    ("contains a skipped audio language", "audio_lang"),
    # LIMIT_TO_PREFERRED_AUDIO_LANGUAGES
    ("no preferred audio tracks", "audio_lang"),
    # SKIP_IF_INTERNAL_SUBTITLES_LANGUAGE
    ("internal subtitles in", "internal_sub_lang"),
    # SKIP_SUBTITLE_LANGUAGES
    ("contains a skipped subtitle language", "internal_sub_lang"),
    # SKIP_UNKNOWN_LANGUAGE / SKIP_IF_NO_LANGUAGE_BUT_SUBTITLES_EXIST
    ("audio language unknown", "unknown_audio_lang"),
)


def _classify_subgen_log_reason(reason: str) -> str:
    """Map a subgen `Skipping <name>: <reason>` log suffix to a skip_reason
    enum value. Returns 'unknown' if no phrase matches."""
    low = reason.lower()
    for phrase, enum_val in _SKIP_REASON_PHRASES:
        if phrase in low:
            return enum_val
    return "unknown"


def _fs_skip_reason_heuristic(canonical_path: str) -> str:
    """Fallback for when the subgen log read failed or didn't include a
    line for this basename. If an .srt sits next to the video file the
    user submitted, classify as 'sub_exists'; otherwise 'unknown'."""
    try:
        fs_path = canonical_to_fs(canonical_path)
    except (PathOutsideRootError, OSError):
        return "unknown"
    # canonical_path may point at a directory (season folder); we can't
    # cheaply pick THE file subgen tripped on from a directory, so
    # conservatively report 'unknown'.
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
            if name.startswith(stem + ".") and name.lower().endswith(".srt"):
                return "sub_exists"
    except OSError:
        return "unknown"
    return "unknown"


def _infer_skip_reason(canonical_path: str, body: dict | None) -> tuple[str, str | None]:
    """Best-effort disambiguation of subgen's single 'skipped' counter.

    Two-stage resolution, in priority order:

    1. **subgen log** (authoritative). scan_runner attaches a
       `skip_reasons: {basename: reason_string}` map to subgen_body
       right after /batch returns, tailed from the subgen container log.
       We look up the canonical_path's basename and classify the reason
       phrase via _classify_subgen_log_reason. Covers every
       should_skip_file branch — audio_lang, internal_sub_lang,
       sub_exists, lrc, no_preferred_audio, unknown_audio_lang.

    2. **Filesystem heuristic** (fallback). When the log read failed
       (docker down, log line rotated, basename collision) we check for
       a sibling .srt — catches sub_exists but nothing else.

    Returns (skip_reason, raw_log_reason_or_None). The raw log string is
    handed back so the UI can show it verbatim in the detail line — far
    more useful than a generic "audio-lang match" when the user wants to
    know WHICH language was skipped.

    Enum values: 'sub_exists' | 'audio_lang' | 'internal_sub_lang' |
    'unknown_audio_lang' | 'unknown'.
    """
    raw: str | None = None
    if isinstance(body, dict):
        skip_map = body.get("skip_reasons")
        if isinstance(skip_map, dict) and canonical_path:
            basename = os.path.basename(canonical_path)
            raw = skip_map.get(basename)
            if isinstance(raw, str) and raw:
                classified = _classify_subgen_log_reason(raw)
                if classified != "unknown":
                    return classified, raw
                # Log line found but we don't recognise the phrase — still
                # return the raw text so the UI can show it. Reason stays
                # 'unknown' which routes to Issues, which is appropriate
                # for unrecognised reasons.
                return "unknown", raw
    # Fallback to filesystem heuristic; raw stays None.
    return _fs_skip_reason_heuristic(canonical_path) if canonical_path else "unknown", None


def _path_outcome_chip(
    status: str, body: dict | None, error: str | None,
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
        return {"category": "running", "label": "running",
                "detail": "scan_runner forwarding to subgen"}
    if status == PATH_STATUS_OK:
        queued = (body or {}).get("queued", 0) if isinstance(body, dict) else 0
        return {"category": "ok", "label": "queued",
                "detail": f"subgen queued {queued}" if queued else "subgen accepted"}
    if status == PATH_STATUS_SKIPPED:
        skip_reason, raw = _infer_skip_reason(canonical_path or "", body)
        # Pick the label + detail. When subgen gave us the raw log reason
        # we prefer it verbatim — it names the actual language / branch,
        # which is more actionable than a generic enum description.
        if skip_reason == "sub_exists":
            label = "sub already exists"
            detail = raw or ("matching .srt already on disk — subgen had "
                             "nothing to do. Not an issue.")
        elif skip_reason == "audio_lang":
            label = "audio-lang skip"
            detail = raw or ("audio language is in your SKIP_IF_AUDIO_LANGUAGES "
                             "list. Verify the file's actual language if "
                             "this surprised you.")
        elif skip_reason == "internal_sub_lang":
            label = "internal sub skip"
            detail = raw or ("internal subtitle language matched a skip "
                             "rule (SKIP_IF_INTERNAL_SUBTITLES_LANGUAGE / "
                             "SKIP_SUBTITLE_LANGUAGES).")
        elif skip_reason == "unknown_audio_lang":
            label = "unknown audio lang"
            detail = raw or ("subgen couldn't detect the audio language "
                             "and SKIP_UNKNOWN_LANGUAGE is enabled.")
        else:
            label = "skipped"
            detail = (raw or error
                      or "subgen walked but queued nothing — reason not "
                         "found in container log. See subgen logs for the "
                         "per-file reason.")
        return {"category": "skipped", "label": label,
                "detail": detail, "skip_reason": skip_reason}
    if status == PATH_STATUS_ERROR:
        return {"category": "error", "label": "failed",
                "detail": error or "submission errored"}
    if status == PATH_STATUS_ORPHANED:
        # #229 phase 2: subgen restarted between subarr's accept and the
        # .srt landing on disk. Surface in its own category so the Queue
        # UI can route to a "Lost on restart" bucket and show a prominent
        # requeue button rather than burying these in Recently-done.
        return {"category": "orphaned", "label": "lost on restart",
                "detail": error or
                "subgen restarted before transcription completed"}
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
            # it'll be visible in the processing/queued section already.
            if basename in live_paths and r.status == PATH_STATUS_RUNNING:
                continue
            outcome = _path_outcome_chip(
                r.status, r.subgen_body, r.error, canonical_path=r.path,
            )
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
    store = request.app.state.scans
    runner = request.app.state.runner
    scan = store.create([canonical], reverse=req.reverse)
    runner.start(scan, audio_language_override=audio_language_override)
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
