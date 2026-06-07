"""#66/#116 slice 2: the pending-queue feeder.

Drains subarr's pending_queue into subgen one job at a time, keeping subgen at a
target depth. This is what turns the dormant store (slice 1) into the queue
authority: instead of flooding subgen, subarr holds the backlog (reorderable,
pausable) and feeds it in only as subgen frees up.

subgen's queue is treated as SHARED:
- depth is measured on subgen's TOTAL (queued + processing, foreign items
  included) — if another producer fills subgen, we back off.
- before submitting we dedup against what's already in subgen's queue (a foreign
  producer may have grabbed the same file) → adopt-as-submitted, never double.

Submission goes through an injected `submit_job` callable (in production: create
a 1-path scan + scan_runner.start, so all existing provenance/completion
plumbing applies). The feeder marks the job `submitted` on success; the existing
completion_watcher reconciles completion by canonical_path as it does today.

Supervised via #157: the loop reports per-tick success/failure to task_health
("queue-feeder"). subgen being unreachable is a soft skip, not a feeder failure.
"""
from __future__ import annotations

import asyncio
import logging

from .paths import canonical_to_subgen_batch
from .pending_queue import PendingQueueStore, STATUS_ERROR
from .subgen_client import SubgenUnavailable

log = logging.getLogger(__name__)

FEEDER_INTERVAL_S = 5.0
DEFAULT_TARGET_DEPTH = 2


def _subgen_paths(q: dict) -> set[str]:
    """The set of subgen-space paths currently queued/processing (same shape
    the completion_watcher matches on: list of {'path': ...} dicts)."""
    out: set[str] = set()
    for t in (q.get("queued") or []) + (q.get("processing") or []):
        if isinstance(t, dict) and t.get("path"):
            out.add(t["path"])
    return out


def _effective_depth(q: dict) -> int:
    queued = q.get("queued_count")
    processing = q.get("processing_count")
    if isinstance(queued, int) and isinstance(processing, int):
        return queued + processing
    return len(q.get("queued") or []) + len(q.get("processing") or [])


class PendingQueueFeeder:
    def __init__(
        self, *, store: PendingQueueStore, subgen_provider, submit_job,
        target_depth_provider=lambda: DEFAULT_TARGET_DEPTH,
        paused_provider=lambda: False,
        interval_s: float = FEEDER_INTERVAL_S,
    ):
        self._store = store
        self._subgen_provider = subgen_provider
        self._submit_job = submit_job              # async (job) -> None; raises on failure
        self._target_depth = target_depth_provider
        self._paused = paused_provider
        self._interval_s = interval_s
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._health = None  # set by app lifespan (#157)

    @property
    def _subgen(self):
        return self._subgen_provider()

    # ── lifecycle ───────────────────────────────────────────────────

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="subarr-queue-feeder")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self) -> None:
        log.info("queue feeder started (interval=%ss)", self._interval_s)
        while not self._stop.is_set():
            try:
                await self.tick()
                _h = getattr(self, "_health", None)
                if _h:
                    _h.record_success("queue-feeder", expected_interval_s=self._interval_s)
            except Exception as e:  # noqa: BLE001
                _h = getattr(self, "_health", None)
                if _h:
                    _h.record_failure("queue-feeder", e, expected_interval_s=self._interval_s)
                log.exception("queue feeder tick failed: %s", e)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_s)
            except asyncio.TimeoutError:
                pass
        log.info("queue feeder stopped")

    # ── one tick (public for tests) ─────────────────────────────────

    async def tick(self) -> int:
        """Submit pending jobs until subgen reaches target depth. Returns the
        number submitted this tick."""
        if self._paused():
            return 0
        subgen = self._subgen
        if subgen is None:
            return 0
        try:
            q = await subgen.queue()
        except (SubgenUnavailable, Exception) as e:  # noqa: BLE001
            # subgen down / queue unreadable → soft skip; the feeder loop itself
            # is healthy. Try again next tick.
            log.debug("queue feeder: subgen queue unreadable, skipping tick: %s", e)
            return 0

        effective = _effective_depth(q)
        subgen_paths = _subgen_paths(q)
        target = max(0, int(self._target_depth()))
        submitted = 0

        while effective < target:
            jobs = self._store.next_pending(1)
            if not jobs:
                break
            job = jobs[0]
            sg_path = canonical_to_subgen_batch(job.canonical_path)
            # Already in subgen's queue (a foreign producer, or a prior tick) —
            # adopt as submitted instead of double-queueing.
            if sg_path in subgen_paths:
                self._store.mark_submitted(job.id)
                subgen_paths.discard(sg_path)
                effective += 1
                submitted += 1
                continue
            try:
                await self._submit_job(job)
            except Exception as e:  # noqa: BLE001 — one bad job mustn't stall the queue
                log.warning("queue feeder: submit failed for %s: %s",
                            job.canonical_path, e)
                self._store.set_status(job.id, STATUS_ERROR, error=str(e)[:500])
                continue  # don't consume a depth slot; move to the next job
            self._store.mark_submitted(job.id)
            effective += 1
            submitted += 1
        return submitted
