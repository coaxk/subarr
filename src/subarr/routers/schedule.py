"""Schedule + auto-queue rules HTTP API."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..auto_queue import evaluate
from ..coverage_engine import build_coverage
from ..schedule_store import AutoQueueRules

router = APIRouter(prefix="/api", tags=["schedule"])
log = logging.getLogger(__name__)


class ScheduleUpdate(BaseModel):
    enabled: bool | None = None
    kind: str | None = None
    interval_minutes: int | None = None
    daily_hhmm: str | None = None
    day_of_week: int | None = None


class RulesUpdate(BaseModel):
    mode: str | None = None
    min_score: int | None = None
    allow_languages: list[str] | None = None
    deny_languages: list[str] | None = None
    allow_tags: list[str] | None = None
    deny_tags: list[str] | None = None
    require_monitored: bool | None = None
    skip_stale_disk: bool | None = None
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
async def run_now(request: Request) -> dict[str, Any]:
    scheduler = request.app.state.scheduler
    return await scheduler.run_coverage_walk()


@router.post("/schedule/preview")
async def preview(request: Request) -> dict[str, Any]:
    """Dry-run: pull coverage + evaluate current rules WITHOUT enqueueing.
    Lets the UI show 'what would be auto-queued right now?'."""
    bundle = request.app.state.integrations
    rules = request.app.state.schedule.get_rules()
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
