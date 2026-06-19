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
import time

from .paths import canonical_to_subgen_batch
from .pending_queue import PendingQueueStore, STATUS_ERROR
from .subgen_client import SubgenUnavailable

log = logging.getLogger(__name__)

FEEDER_INTERVAL_S = 5.0
DEFAULT_TARGET_DEPTH = 2
# How long a just-submitted job reserves a depth slot while subgen hasn't yet
# surfaced it in /queue. subgen's /batch is async (it scans the folder before
# the job appears), so without this reservation the feeder re-fills the same
# slot every tick and overshoots the target depth. The slot is released as soon
# as the job appears in subgen's queue, or after this grace window (covers a
# very short job that completed before it ever showed up).
INFLIGHT_GRACE_S = 30.0


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


def _processing_count(q: dict) -> int:
    p = q.get("processing_count")
    return p if isinstance(p, int) else len(q.get("processing") or [])


class PendingQueueFeeder:
    def __init__(
        self,
        *,
        store: PendingQueueStore,
        subgen_provider,
        submit_job,
        target_depth_provider=lambda: DEFAULT_TARGET_DEPTH,
        paused_provider=lambda: False,
        caps_provider=lambda: None,
        arena_inflight_provider=lambda: 0,
        interval_s: float = FEEDER_INTERVAL_S,
    ):
        self._store = store
        self._subgen_provider = subgen_provider
        self._submit_job = submit_job  # async (job) -> None; raises on failure
        self._target_depth = target_depth_provider
        self._paused = paused_provider
        self._caps = caps_provider
        self._arena_inflight = arena_inflight_provider
        self._interval_s = interval_s
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        # #169: set by kick() to wake the loop for an immediate tick (a manual
        # submit / requeue just enqueued) instead of waiting out interval_s.
        self._wake = asyncio.Event()
        self._health = None  # set by app lifespan (#157)
        # subgen-space paths submitted but not yet surfaced in subgen's /queue,
        # → monotonic submit time. Counted toward effective depth so we don't
        # re-fill a slot subgen hasn't reported yet (the over-feed race).
        self._inflight: dict[str, float] = {}

    @property
    def _subgen(self):
        return self._subgen_provider()

    # ── lifecycle ───────────────────────────────────────────────────

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="subarr-queue-feeder")

    def kick(self) -> None:
        """Wake the feeder for an immediate tick instead of waiting up to
        interval_s. Called when a producer enqueues urgent work (manual submit /
        requeue, #169) so it starts as soon as there's a depth slot — manual is
        the top priority bucket, so it's fed first."""
        self._wake.set()

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()  # break the interval wait so the loop notices stop now
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
            # Clear BEFORE the tick so a kick() arriving mid-tick still triggers
            # the next iteration (no missed wake).
            self._wake.clear()
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
            if self._stop.is_set():
                break
            # Sleep until interval_s elapses OR kick() wakes us early.
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval_s)
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

        subgen_paths = _subgen_paths(q)
        now = time.monotonic()
        # Release in-flight reservations that have surfaced in subgen's queue
        # (handed off to _effective_depth) or aged past the grace window.
        self._inflight = {
            p: t for p, t in self._inflight.items() if p not in subgen_paths and (now - t) < INFLIGHT_GRACE_S
        }
        # Effective depth = subgen's reported depth PLUS our not-yet-surfaced
        # submissions, so a tick landing in the /batch→/queue lag window doesn't
        # re-fill a slot it already filled (the over-feed race).
        effective = _effective_depth(q) + len(self._inflight)
        target = max(0, int(self._target_depth()))
        submitted = 0

        from .subgen_capacity import subgen_capacity_free

        caps = self._caps()
        n = getattr(caps, "concurrent_transcriptions", None) if caps else None

        while effective < target and subgen_capacity_free(
            processing_count=_processing_count(q) + len(self._inflight),
            arena_in_flight=self._arena_inflight(),
            n=n,
        ):
            jobs = self._store.next_pending(1)
            if not jobs:
                break
            job = jobs[0]
            sg_path = canonical_to_subgen_batch(job.canonical_path)
            # Already in subgen's queue (a foreign producer, or a prior tick) —
            # adopt as submitted instead of double-queueing.
            if sg_path in subgen_paths:
                self._store.mark_submitted(job.id)
                self._inflight.pop(sg_path, None)
                subgen_paths.discard(sg_path)
                effective += 1
                submitted += 1
                continue
            try:
                await self._submit_job(job)
            except Exception as e:  # noqa: BLE001 — one bad job mustn't stall the queue
                log.warning("queue feeder: submit failed for %s: %s", job.canonical_path, e)
                self._store.set_status(job.id, STATUS_ERROR, error=str(e)[:500])
                continue  # don't consume a depth slot; move to the next job
            self._store.mark_submitted(job.id)
            self._inflight[sg_path] = now  # reserve the slot until subgen surfaces it
            effective += 1
            submitted += 1
        return submitted
