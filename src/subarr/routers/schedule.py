"""Schedule + auto-queue rules HTTP API."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from dataclasses import fields as _dc_fields

from ..auto_queue import evaluate
from ..coverage_engine import CoverageItem, build_coverage
from ..schedule_store import AutoQueueRules

# Constructor fields of CoverageItem — used to rebuild items from the
# cached coverage snapshot (stored as dicts) without a fresh 60-90s build.
_COVERAGE_ITEM_FIELDS = {f.name for f in _dc_fields(CoverageItem)}

router = APIRouter(prefix="/api", tags=["schedule"])
log = logging.getLogger(__name__)


class ScheduleUpdate(BaseModel):
    enabled: bool | None = None
    kind: str | None = None
    interval_minutes: int | None = None
    daily_hhmm: str | None = None
    day_of_week: int | None = None
    probe_roots: str | None = None  # comma-separated canonical paths


class RulesUpdate(BaseModel):
    mode: str | None = None
    min_score: int | None = None
    allow_languages: list[str] | None = None
    deny_languages: list[str] | None = None
    allow_tags: list[str] | None = None
    deny_tags: list[str] | None = None
    require_monitored: bool | None = None
    skip_stale_disk: bool | None = None
    skip_embedded_en: bool | None = None
    max_per_run: int | None = None


@router.get("/schedule")
async def get_schedule(request: Request) -> dict[str, Any]:
    store = request.app.state.schedule
    return {
        "schedules": [s.to_dict() for s in store.list_schedules()],
        "rules": store.get_rules().to_dict(),
    }


@router.patch("/schedule/{name}")
async def update_schedule(name: str, req: ScheduleUpdate, request: Request) -> dict[str, Any]:
    store = request.app.state.schedule
    try:
        updated = store.update_schedule(
            name,
            enabled=req.enabled,
            kind=req.kind,
            interval_minutes=req.interval_minutes,
            daily_hhmm=req.daily_hhmm,
            day_of_week=req.day_of_week,
            probe_roots=req.probe_roots,
        )
    except KeyError as e:
        raise HTTPException(404, detail=str(e))
    return updated.to_dict()


@router.put("/schedule/rules")
async def put_rules(req: RulesUpdate, request: Request) -> dict[str, Any]:
    store = request.app.state.schedule
    current = store.get_rules().to_dict()
    payload = req.model_dump(exclude_none=True)
    current.update(payload)
    new_rules = AutoQueueRules.from_dict(current)
    store.set_rules(new_rules)
    return new_rules.to_dict()


@router.post("/schedule/coverage_walk/run-now")
async def run_now(request: Request, wait: bool = False) -> dict[str, Any]:
    """Kick off a coverage walk in the background and return immediately.

    Previously this awaited the entire walk synchronously, which on a
    real-size library takes minutes hitting Bazarr/Sonarr/Radarr APIs,
    and the HTTP request would hang past every reasonable browser/curl
    timeout. The Dashboard welcome card 'Run now' button looked broken
    because of this — the click fires the fetch, the fetch never settles,
    no alert ever shows.

    The walk's progress is observable elsewhere (Coverage page, dashboard
    DISCOVERED tile, scan_store history) — there's no value in blocking
    the HTTP response on it. Caller gets an immediate {started: true}.

    `wait=true` forces synchronous behaviour — used by tests + any
    automation that needs the structured walk result (mode, queued count,
    pending_walk_id for manual_confirm flows, etc.).
    """
    scheduler = request.app.state.scheduler
    if wait:
        return await scheduler.run_coverage_walk()
    asyncio.create_task(scheduler.run_coverage_walk())
    return {"started": True}


class ApproveRequest(BaseModel):
    decision_ids: list[int] | None = None  # None / empty = approve all


@router.get("/schedule/pending")
async def list_pending(request: Request, status: str | None = None) -> dict[str, Any]:
    store = request.app.state.pending
    walks = store.list_walks(status=status)
    # Return each walk's decisions inline for convenience.
    out = []
    for w in walks:
        full = store.get_walk(w.id)
        if full:
            out.append(full.to_dict())
    return {"pending_count": store.pending_count(), "walks": out}


@router.post("/schedule/pending/{walk_id}/approve")
async def approve_pending(walk_id: str, req: ApproveRequest, request: Request) -> dict[str, Any]:
    scheduler = request.app.state.scheduler
    return await scheduler.approve_pending(walk_id, req.decision_ids)


@router.post("/schedule/pending/{walk_id}/reject")
async def reject_pending(walk_id: str, req: ApproveRequest, request: Request) -> dict[str, Any]:
    scheduler = request.app.state.scheduler
    return await scheduler.reject_pending(walk_id, req.decision_ids)


@router.post("/schedule/preview")
async def preview(request: Request) -> dict[str, Any]:
    """Dry-run: evaluate current rules against the latest coverage WITHOUT
    enqueueing. Lets the UI show 'what would be auto-queued right now?'.

    Reads the background-refreshed coverage snapshot (sub-second) and
    reconstructs CoverageItem objects from it, rather than triggering a
    fresh 60-90s build on every click. Falls back to a synchronous build
    only when the cache hasn't warmed yet (first boot)."""
    bundle = request.app.state.integrations
    rules = request.app.state.schedule.get_rules()
    cov_cache = getattr(request.app.state, "coverage_cache", None)
    snap = cov_cache.get_cached() if cov_cache is not None else None
    if snap is not None:
        items = [
            CoverageItem(**{k: v for k, v in d.items() if k in _COVERAGE_ITEM_FIELDS})
            for d in snap.items
        ]

        class _Report:
            pass
        report = _Report()
        report.items = items
    else:
        report = await build_coverage(bundle, use_tautulli=True)
    decisions = evaluate(report.items, rules)
    queue = [d.to_dict() for d in decisions if d.action == "queue"]
    skip_sample = [d.to_dict() for d in decisions if d.action == "skip"][:20]
    return {
        "considered": len(report.items),
        "would_queue": len(queue),
        "would_skip": sum(1 for d in decisions if d.action == "skip"),
        "queue_preview": queue[:50],
        "skip_sample": skip_sample,
        "rules": rules.to_dict(),
    }
