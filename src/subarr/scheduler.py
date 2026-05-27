"""Scheduler: background asyncio loop that fires scheduled jobs.

One canonical job for v1.2 batch 1: 'coverage_walk'. When fired, the
scheduler:
  1. Builds a fresh CoverageReport (bypassing the /api/coverage cache).
  2. Loads AutoQueueRules from the schedule store.
  3. Runs auto_queue.evaluate() to get per-item decisions.
  4. For each 'queue' decision: invokes the same internal resolve+enqueue
     path as /api/coverage/queue.
  5. Records last_run_at / next_run_at / last_result.

We deliberately do NOT use APScheduler — the cadence model here is
small (interval / daily / weekly) and a hand-rolled tick avoids another
dep + thread pool.

Tick frequency: every 60s. The loop sleeps that long, then asks each
enabled schedule "are you due to fire now?" via `_due()`.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import time
from typing import Any

from .auto_queue import Decision, evaluate
from .coverage_engine import IntegrationBundle, build_coverage
from .integrations import IntegrationError
from .paths import PathOutsideRootError, canonical_to_fs
from .provenance import SOURCE_SUBGENSCAN, ProvenanceStore
from .scan_runner import ScanRunner
from .scan_store import ScanStore
from .schedule_store import (
    KIND_DAILY, KIND_INTERVAL, KIND_WEEKLY,
    ScheduleConfig, ScheduleStore,
)

log = logging.getLogger(__name__)

TICK_S = 60


def _strip_arr_prefix(arr_path: str) -> str:
    from .config import settings
    prefix = settings.arr_path_prefix
    s = arr_path or ""
    if prefix and s.startswith(prefix):
        s = s[len(prefix):]
    return s.strip("/")


def _due(sched: ScheduleConfig, now: datetime.datetime, last_run_at: float | None) -> bool:
    """Has the schedule fired more than its cadence-worth ago?"""
    if not sched.enabled:
        return False
    if sched.kind == KIND_INTERVAL:
        if last_run_at is None:
            return True
        return (time.time() - last_run_at) >= sched.interval_minutes * 60
    if sched.kind in {KIND_DAILY, KIND_WEEKLY}:
        try:
            hh, mm = sched.daily_hhmm.split(":")
            target_h, target_m = int(hh), int(mm)
        except (ValueError, AttributeError):
            return False
        target_today = now.replace(hour=target_h, minute=target_m, second=0, microsecond=0)
        # Did we already run after today's target time?
        if last_run_at is not None:
            last_dt = datetime.datetime.fromtimestamp(last_run_at)
            if sched.kind == KIND_DAILY:
                if last_dt >= target_today:
                    return False
            else:  # weekly
                # last_run must be on a different week-of-year OR before this week's target
                if last_dt.isocalendar()[:2] == now.isocalendar()[:2] and last_dt >= target_today:
                    return False
        if sched.kind == KIND_WEEKLY and now.weekday() != sched.day_of_week:
            return False
        return now >= target_today
    return False


def _next_run_for(sched: ScheduleConfig, now: datetime.datetime) -> float | None:
    if not sched.enabled:
        return None
    if sched.kind == KIND_INTERVAL:
        return time.time() + sched.interval_minutes * 60
    try:
        hh, mm = sched.daily_hhmm.split(":")
        target_h, target_m = int(hh), int(mm)
    except (ValueError, AttributeError):
        return None
    candidate = now.replace(hour=target_h, minute=target_m, second=0, microsecond=0)
    if candidate <= now:
        candidate = candidate + datetime.timedelta(days=1)
    if sched.kind == KIND_WEEKLY:
        # advance to the configured day_of_week
        days_to_add = (sched.day_of_week - candidate.weekday()) % 7
        candidate = candidate + datetime.timedelta(days=days_to_add)
    return candidate.timestamp()


class Scheduler:
    def __init__(
        self,
        schedule_store: ScheduleStore,
        bundle: IntegrationBundle,
        scan_store: ScanStore,
        runner: ScanRunner,
        provenance: ProvenanceStore,
        tick_s: int = TICK_S,
    ):
        self._schedule = schedule_store
        self._bundle = bundle
        self._scan_store = scan_store
        self._runner = runner
        self._provenance = provenance
        self._tick_s = tick_s
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="subarr-scheduler")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self) -> None:
        log.info("scheduler started (tick=%ds)", self._tick_s)
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception as e:
                log.exception("scheduler tick failed: %s", e)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._tick_s)
            except asyncio.TimeoutError:
                pass
        log.info("scheduler stopped")

    async def _tick(self) -> None:
        now = datetime.datetime.now()
        for sched in self._schedule.list_schedules():
            if not _due(sched, now, sched.last_run_at):
                continue
            log.info("scheduler firing %s", sched.name)
            if sched.name == "coverage_walk":
                result = await self.run_coverage_walk()
            else:
                result = {"skipped": f"unknown schedule {sched.name!r}"}
            ts = time.time()
            self._schedule.record_run(
                sched.name,
                last_run_at=ts,
                next_run_at=_next_run_for(sched, datetime.datetime.now()),
                last_result=str(result)[:1000],
            )

    async def run_coverage_walk(self) -> dict[str, Any]:
        """Public entry point — also called by POST /api/schedule/run-now."""
        rules = self._schedule.get_rules()
        try:
            report = await build_coverage(self._bundle, use_tautulli=True)
        except Exception as e:
            log.exception("coverage_walk: build_coverage failed: %s", e)
            return {"ok": False, "error": str(e)}

        decisions = evaluate(report.items, rules)
        queued = 0
        errors: list[str] = []
        scan_ids: list[str] = []
        for d in decisions:
            if d.action != "queue":
                continue
            scan_id, err = await self._enqueue(d)
            if scan_id:
                queued += 1
                scan_ids.append(scan_id)
            if err:
                errors.append(err)

        log.info(
            "coverage_walk: %d items considered, %d queued, %d errors",
            len(report.items), queued, len(errors),
        )
        return {
            "ok": True,
            "considered": len(report.items),
            "decisions_queue": sum(1 for d in decisions if d.action == "queue"),
            "decisions_skip": sum(1 for d in decisions if d.action == "skip"),
            "queued": queued,
            "errors": errors[:10],
            "scan_ids": scan_ids[:20],
            "mode": rules.mode,
        }

    async def _enqueue(self, decision: Decision) -> tuple[str | None, str | None]:
        """Mirror of routers/coverage_actions logic but inline so the scheduler
        doesn't have to talk to itself over HTTP."""
        item = decision.item
        canonical: str | None = None
        series_id: int | None = None

        if item.bazarr_episode_id and self._bundle.sonarr.is_configured():
            try:
                ep = await self._bundle.sonarr.episode(item.bazarr_episode_id)
                series_id = ep.get("seriesId")
                ep_file_id = ep.get("episodeFileId")
                if ep_file_id:
                    ep_file = await self._bundle.sonarr.episode_file(ep_file_id)
                    arr_path = ep_file.get("path")
                    if arr_path:
                        canonical = _strip_arr_prefix(arr_path)
            except IntegrationError as e:
                return None, f"{item.title}: sonarr resolve failed: {e}"

        if not canonical:
            canonical = item.canonical_path

        if not canonical:
            return None, f"{item.title}: no canonical path"

        try:
            target = canonical_to_fs(canonical)
        except PathOutsideRootError:
            return None, f"{item.title}: path escapes media root"

        if not target.exists():
            return None, f"{item.title}: {canonical!r} missing on disk"

        scan = self._scan_store.create([canonical], reverse=False)
        self._runner.start(scan)
        self._provenance.record(
            canonical_path=canonical,
            scan_id=scan.id,
            source=SOURCE_SUBGENSCAN,
            series_id=series_id,
            sonarr_episode_id=item.bazarr_episode_id,
        )
        return scan.id, None
