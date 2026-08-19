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
from .paths import PathOutsideRootError, canonical_to_fs, library_for_canonical, strip_arr_prefix
from .pending_store import PendingStore
from .probe_walker import ProbeWalker
from .provenance import SOURCE_SUBGENSCAN, ProvenanceStore
from .scan_runner import ScanRunner
from .scan_store import ScanStore
from .schedule_store import (
    KIND_DAILY,
    KIND_INTERVAL,
    KIND_WEEKLY,
    MODE_MANUAL_CONFIRM,
    ScheduleConfig,
    ScheduleStore,
)

log = logging.getLogger(__name__)

TICK_S = 60


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
        bundle: IntegrationBundle | None = None,
        scan_store: ScanStore = None,
        runner: ScanRunner = None,
        provenance: ProvenanceStore = None,
        probe_walker: ProbeWalker | None = None,
        pending_store: PendingStore | None = None,
        pending_queue=None,  # #66/#116 slice 6: route auto-queue through the feeder
        tick_s: int = TICK_S,
        bundle_provider=None,
        caps_provider=None,
    ):
        self._schedule = schedule_store
        # Resolve the integration bundle live so onboarding live-reload can
        # swap clients on app.state without restarting the scheduler.
        # Backward compatible: a directly-passed bundle becomes a constant
        # provider.
        self._bundle_provider = bundle_provider or (lambda: bundle)
        # #79: resolve subgen caps live so the coverage_walk's forced-only-EN
        # gate tracks the runtime IGNORE_FORCED_SUBTITLES value. Backward
        # compatible — no provider → caps None → forced-only rows treated as
        # non-actionable (the safe default).
        self._caps_provider = caps_provider or (lambda: None)
        self._scan_store = scan_store
        self._runner = runner
        self._provenance = provenance
        self._probe_walker = probe_walker
        self._pending = pending_store
        self._pending_queue = pending_queue
        self._tick_s = tick_s
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        # Coverage walks are expensive (full Sonarr/Radarr/Bazarr API
        # iteration over the entire library). Stacking concurrent walks
        # multiplies upstream-API load and produces no useful work — they
        # all see the same Bazarr-wanted snapshot. Lock so only one runs
        # at a time; clicks while a walk is in flight return immediately
        # with already_running=True.
        self._walk_lock = asyncio.Lock()

    @property
    def _bundle(self):
        """Current integration bundle (resolved live for onboarding reload)."""
        return self._bundle_provider()

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
                _h = getattr(self, "_health", None)  # #157 supervision hook
                if _h:
                    _h.record_success("scheduler", expected_interval_s=self._tick_s)
            except Exception as e:
                _h = getattr(self, "_health", None)
                if _h:
                    _h.record_failure("scheduler", e, expected_interval_s=self._tick_s)
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

    async def _run_probe_walks(self, roots: list[str]) -> dict[str, Any]:
        """Chain incremental probe walks before coverage. Each root's walker
        skips unchanged files via mtime/size cache check, so a re-walk is
        mostly stat() calls — cheap. Errors don't abort coverage_walk —
        we log + return partial stats."""
        if not self._probe_walker or not roots:
            return {"ran": False, "reason": "no probe walker or no roots configured"}
        results = []
        for root in roots:
            try:
                state = await self._probe_walker.start_walk(root)
                # Wait for terminal state. start_walk returns immediately
                # (asyncio.create_task); poll the state object until status
                # is no longer 'running'. We don't subscribe via SSE here
                # because that's a frontend channel.
                while state.status == "running":
                    await asyncio.sleep(0.5)
                results.append(
                    {
                        "root": root,
                        "status": state.status,
                        "total": state.total_files,
                        "probed": state.probed,
                        "cached_hits": state.cached_hits,
                        "errors": len(state.errors),
                    }
                )
            except Exception as e:
                log.warning("probe walk %r failed: %s", root, e)
                results.append({"root": root, "status": "error", "error": str(e)})
        return {"ran": True, "walks": results}

    # Rate-limit Bazarr scan-disk pokes — Bazarr's task is library-wide,
    # firing it every 30s would be pointless and noisy. 5 min keeps things
    # snappy enough for interactive testing while avoiding spam.
    _BAZARR_POKE_COOLDOWN_S = 300

    async def _maybe_poke_bazarr_for_stale_disk(self, items: list) -> dict[str, Any]:
        """Fire Bazarr scan-disk if the walk found stale-disk items.

        Stale-disk means subarr discovered an existing .srt on disk that
        Bazarr's wanted-list-of-record doesn't know about. Telling Bazarr
        to re-scan is the only way to get those items off the wanted list
        (Bazarr only re-checks disk on its scheduled cadence otherwise).

        Returns: a dict logged for the run-now JSON response so users can see
        what happened. Two shapes: the no-work early-out is
        {fired: False, reason: "no stale-disk items"}; otherwise
        {fired, stale_count, instances: [{instance, fired, reason|task_id, count}]}
        — fired/skip reasons are per-instance (#371), not a single top-level reason.
        """
        stale = [it for it in items if getattr(it, "suggest_bazarr_rescan", False)]
        if not stale:
            return {"fired": False, "reason": "no stale-disk items"}

        # #371: per-instance cooldown. A recent poke on one Bazarr instance must
        # not gate a DIFFERENT instance whose rows just went stale, and a failed
        # instance must not be left under cooldown — so the cooldown is keyed per
        # instance (same base_url key as the task-id cache) and only advances for
        # an instance that actually fired. (Was a single global timestamp.)
        now = time.time()
        cooldowns = getattr(self, "_bazarr_poke_ts", None)
        if cooldowns is None:
            cooldowns = self._bazarr_poke_ts = {}

        # #161 P3: group stale rows by the Bazarr instance that owns their
        # library and fire one scan-disk per instance (was a single trigger on
        # instance 0). Single-stack: one instance → unchanged.
        bundle = self._bundle
        by_inst: dict[str, dict] = {}
        for it in stale:
            canonical = getattr(it, "file_canonical_path", None) or getattr(it, "canonical_path", None) or ""
            bz = (
                bundle.client_for("bazarr", library_for_canonical(canonical).bazarr_id)
                if canonical
                else bundle.bazarr
            )
            key = getattr(bz, "_base_url", "") or str(id(bz))
            slot = by_inst.setdefault(key, {"client": bz, "count": 0})
            slot["count"] += 1

        fired_any = False
        instances: list[dict] = []
        for key, slot in by_inst.items():
            bz = slot["client"]
            if not bz.is_configured():
                instances.append({"instance": key, "fired": False, "reason": "bazarr not configured"})
                continue
            # #371: skip an instance still inside its own cooldown window.
            since = now - cooldowns.get(key, 0.0)
            if since < self._BAZARR_POKE_COOLDOWN_S:
                instances.append(
                    {
                        "instance": key,
                        "fired": False,
                        "reason": f"cooldown ({int(self._BAZARR_POKE_COOLDOWN_S - since)}s left)",
                        "count": slot["count"],
                    }
                )
                continue
            try:
                task_id = await self._discover_bazarr_scan_task(bz)
            except Exception as e:  # noqa: BLE001 — one bad instance must not abort the poke
                log.warning("coverage_walk: bazarr task discovery failed on %s: %s", key, e)
                instances.append({"instance": key, "fired": False, "reason": f"task discovery failed: {e}"})
                continue
            if not task_id:
                instances.append({"instance": key, "fired": False, "reason": "no scan-disk task id found"})
                continue
            try:
                await bz.trigger_task(task_id)
                fired_any = True
                cooldowns[key] = now  # #371: advance ONLY this instance's cooldown, on success
                instances.append({"instance": key, "fired": True, "task_id": task_id, "count": slot["count"]})
                log.info(
                    "coverage_walk: poked Bazarr %s scan-disk (%s) — %d stale-disk items",
                    key,
                    task_id,
                    slot["count"],
                )
            except Exception as e:  # noqa: BLE001
                log.warning("coverage_walk: bazarr trigger_task failed on %s: %s", key, e)
                instances.append({"instance": key, "fired": False, "reason": str(e)})
        return {"fired": fired_any, "stale_count": len(stale), "instances": instances}

    async def _discover_bazarr_scan_task(self, bz) -> str | None:
        """Find one Bazarr instance's scan-disk task id by hint match."""
        tasks = await bz.list_tasks()
        hints = (
            "series_full_scan_subtitles",
            "movies_full_scan_subtitles",
            "scan_disk_series",
            "scan_disk_episodes",
            "scan disk",
            "index all existing episodes",
        )
        for hint in hints:
            for t in tasks:
                if hint in (t.get("job_id") or "").lower() or hint in (t.get("name") or "").lower():
                    return t.get("job_id")
        return None

    async def run_coverage_walk(self) -> dict[str, Any]:
        """Public entry point — also called by POST /api/schedule/run-now.

        Gated on self._walk_lock: if another walk is in progress this
        returns {already_running: True} immediately without queueing.
        Prevents click-stacking the dashboard Run-now button into N
        concurrent walks that all hit Bazarr/Sonarr/Radarr in parallel.

        Order: incremental probe walks (cheap on re-runs — cached files
        skip ffprobe via mtime+size check) → build_coverage with the
        freshly-refreshed probe cache → evaluate rules → enqueue. Probe
        roots are configured per-schedule (schedule_config.probe_roots,
        comma-separated). Empty means no probe step.
        """
        if self._walk_lock.locked():
            log.info("coverage_walk: skip — another walk already in progress")
            return {"already_running": True}
        async with self._walk_lock:
            return await self._run_coverage_walk_locked()

    async def _run_coverage_walk_locked(self) -> dict[str, Any]:
        rules = self._schedule.get_rules()
        sched = self._schedule.get_schedule("coverage_walk")
        roots_csv = (sched.probe_roots if sched else "") or ""
        roots = [p.strip() for p in roots_csv.split(",") if p.strip()]

        probe_summary: dict[str, Any] = {"ran": False}
        if roots:
            log.info("coverage_walk: probe walks first: %s", roots)
            probe_summary = await self._run_probe_walks(roots)

        try:
            probe_store = getattr(self._probe_walker, "_store", None) if self._probe_walker else None
            report = await build_coverage(
                self._bundle,
                use_tautulli=True,
                probe_store=probe_store,
                subgen_caps=self._caps_provider(),
            )
        except Exception as e:
            log.exception("coverage_walk: build_coverage failed: %s", e)
            return {"ok": False, "error": str(e), "probe": probe_summary}

        # [2026-05-30] Auto-poke Bazarr when the walk discovers stale-disk
        # items (subs already on disk that Bazarr doesn't know about,
        # because of release-name mismatch or external sub source). The
        # completion watcher only fires Bazarr scan-disk after subarr's OWN
        # transcriptions; this covers the "existing sub Bazarr never
        # noticed" case. One library-wide trigger per Bazarr instance —
        # Bazarr's scan-disk is global, no need to fan out per-file. Rate-limited
        # per instance via the _bazarr_poke_ts map so back-to-back walks don't spam.
        bazarr_poked = await self._maybe_poke_bazarr_for_stale_disk(report.items)

        # Build the in-flight set from the provenance ledger so the
        # scheduler doesn't recreate identical pending walks every tick.
        # Bazarr's wanted list doesn't shrink until it sees the .srt on
        # disk; until then the same rows keep matching.
        in_flight = {e.canonical_path for e in self._provenance.pending()}
        # #66/#116 slice 6: also treat pending-queue jobs (not yet submitted to
        # subgen, so not in provenance) as in-flight, so a walk doesn't re-queue
        # what's already waiting in the feeder.
        if self._pending_queue is not None:
            in_flight |= self._pending_queue.active_paths()
        decisions = evaluate(report.items, rules, in_flight_paths=in_flight)
        queue_decisions = [d for d in decisions if d.action == "queue"]

        # manual_confirm: stash the queue decisions for user review;
        # DON'T enqueue. User approves via /api/schedule/pending/{id}/approve.
        if rules.mode == MODE_MANUAL_CONFIRM and self._pending is not None:
            pending = self._pending.create_walk(
                considered=len(report.items),
                items=[d.to_dict() for d in queue_decisions],
            )
            log.info(
                "coverage_walk[manual_confirm]: %d items considered, %d pending approval (walk_id=%s)",
                len(report.items),
                len(queue_decisions),
                pending.id,
            )
            return {
                "ok": True,
                "considered": len(report.items),
                "decisions_queue": len(queue_decisions),
                "decisions_skip": len(decisions) - len(queue_decisions),
                "queued": 0,
                "pending_walk_id": pending.id,
                "pending_count": len(queue_decisions),
                "mode": rules.mode,
                "probe": probe_summary,
            }

        # dashboard / auto_rules: dashboard ends up with zero queue_decisions
        # because evaluate() already skipped everything; auto_rules enqueues.
        queued = 0
        errors: list[str] = []
        scan_ids: list[str] = []
        for d in queue_decisions:
            scan_id, err = await self._enqueue(d)
            if scan_id:
                queued += 1
                scan_ids.append(scan_id)
            if err:
                errors.append(err)

        log.info(
            "coverage_walk: %d items considered, %d queued, %d errors",
            len(report.items),
            queued,
            len(errors),
        )
        return {
            "ok": True,
            "considered": len(report.items),
            "decisions_queue": len(queue_decisions),
            "decisions_skip": len(decisions) - len(queue_decisions),
            "queued": queued,
            "errors": errors[:10],
            "scan_ids": scan_ids[:20],
            "mode": rules.mode,
            "probe": probe_summary,
            "bazarr_poke": bazarr_poked,
        }

    async def approve_pending(self, walk_id: str, decision_ids: list[int] | None = None) -> dict[str, Any]:
        """Enqueue the approved decisions and finalise the walk's status.
        If decision_ids is None or empty, approve all undecided rows."""
        if not self._pending:
            raise RuntimeError("pending store not configured")
        walk = self._pending.get_walk(walk_id)
        if walk is None:
            return {"ok": False, "error": "walk not found"}
        target_ids = set(decision_ids or [])
        approve_all = not target_ids
        queued = 0
        errors: list[str] = []
        scan_ids: list[str] = []
        for d in walk.decisions:
            if d.approved is not None:
                continue  # already decided
            if not approve_all and d.id not in target_ids:
                continue
            # Re-hydrate a Decision-like wrapper to reuse _enqueue.
            scan_id, err = await self._enqueue_from_item(d.item)
            if scan_id:
                queued += 1
                scan_ids.append(scan_id)
                self._pending.mark_decision(d.id, approved=True, scan_id=scan_id)
            else:
                self._pending.mark_decision(d.id, approved=False, scan_id=None)
                if err:
                    errors.append(err)
        final_status = self._pending.finalise_walk(walk_id)
        return {
            "ok": True,
            "walk_id": walk_id,
            "queued": queued,
            "errors": errors[:10],
            "scan_ids": scan_ids[:20],
            "final_status": final_status,
        }

    async def reject_pending(self, walk_id: str, decision_ids: list[int] | None = None) -> dict[str, Any]:
        if not self._pending:
            raise RuntimeError("pending store not configured")
        walk = self._pending.get_walk(walk_id)
        if walk is None:
            return {"ok": False, "error": "walk not found"}
        target_ids = set(decision_ids or [])
        reject_all = not target_ids
        rejected = 0
        for d in walk.decisions:
            if d.approved is not None:
                continue
            if not reject_all and d.id not in target_ids:
                continue
            self._pending.mark_decision(d.id, approved=False)
            rejected += 1
        final_status = self._pending.finalise_walk(walk_id)
        return {"ok": True, "walk_id": walk_id, "rejected": rejected, "final_status": final_status}

    async def _enqueue_from_item(self, item_dict: dict[str, Any]) -> tuple[str | None, str | None]:
        """Mirror of _enqueue but takes the decision-row's item dict
        (since the pending store doesn't preserve CoverageItem objects)."""
        sonarr_ep_id = item_dict.get("sonarr_episode_id")
        canonical = item_dict.get("canonical_path")
        title = item_dict.get("title", "?")
        series_id: int | None = None

        if sonarr_ep_id and self._bundle.sonarr.is_configured():
            try:
                ep = await self._bundle.sonarr.episode(sonarr_ep_id)
                series_id = ep.get("seriesId")
                ep_file_id = ep.get("episodeFileId")
                if ep_file_id:
                    ep_file = await self._bundle.sonarr.episode_file(ep_file_id)
                    arr_path = ep_file.get("path")
                    if arr_path:
                        canonical = strip_arr_prefix(arr_path)
            except IntegrationError as e:
                return None, f"{title}: sonarr resolve failed: {e}"

        if not canonical:
            return None, f"{title}: no canonical path"
        try:
            target = canonical_to_fs(canonical)
        except PathOutsideRootError:
            return None, f"{title}: path escapes media root"
        if not target.exists():
            return None, f"{title}: {canonical!r} missing on disk"

        # #66/#116 slice 6: route through the pending queue (feeder drains it).
        if self._pending_queue is not None:
            job = self._pending_queue.enqueue(
                canonical,
                source="gaps",
                series_id=series_id,
                sonarr_episode_id=sonarr_ep_id,
                submission_origin="gaps",  # #451: scheduler gap-fill origin
            )
            return job.id, None
        scan = self._scan_store.create([canonical], reverse=False)
        self._runner.start(scan)
        self._provenance.record(
            canonical_path=canonical,
            scan_id=scan.id,
            source=SOURCE_SUBGENSCAN,
            series_id=series_id,
            sonarr_episode_id=sonarr_ep_id,
            submission_origin="gaps",
        )
        return scan.id, None

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
                        canonical = strip_arr_prefix(arr_path)
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

        # #66/#116 slice 6: route through the pending queue (the feeder drains
        # to subgen at target depth + writes provenance). Falls back to direct
        # submission if no pending_queue is wired (older callers / tests).
        # #368: movies carry their Radarr id in bazarr_radarr_id (None for
        # episodes) — thread it so completion_watcher can upload the movie .srt
        # to the owning Bazarr instead of only the scan-disk fallback.
        if self._pending_queue is not None:
            job = self._pending_queue.enqueue(
                canonical,
                source="auto",
                series_id=series_id,
                sonarr_episode_id=item.bazarr_episode_id,
                radarr_movie_id=item.bazarr_radarr_id,
                submission_origin="auto",  # #451: scheduler auto-queue origin
            )
            return job.id, None
        scan = self._scan_store.create([canonical], reverse=False)
        self._runner.start(scan)
        self._provenance.record(
            canonical_path=canonical,
            scan_id=scan.id,
            source=SOURCE_SUBGENSCAN,
            series_id=series_id,
            sonarr_episode_id=item.bazarr_episode_id,
            radarr_movie_id=item.bazarr_radarr_id,
            submission_origin="auto",
        )
        return scan.id, None
