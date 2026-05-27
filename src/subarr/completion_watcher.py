"""Background watcher: subgen completion → ledger update + Bazarr write-back.

Polls subgen's /queue every WATCHER_INTERVAL_S seconds. For each pending
ledger entry, asks: is the canonical_path still in subgen's queued or
processing lists? If yes → still in flight, leave alone. If no → consider
the transcribe completed; mark completed_at, then (if the entry has a
series_id) trigger Bazarr's scan-disk-series task so Bazarr picks up the
new .srt without waiting for its periodic scan.

Approach: poll-vs-watch tradeoff.
- POLL is simpler, restart-safe (state lives in the SQLite ledger), and
  precise enough at 30s granularity for transcribes that take 5-15
  minutes per episode.
- WATCH (docker logs subgen --follow + parse 'WORKER FINISH' lines) would
  be lower-latency and more precise per-file, but the streaming
  generator is fragile across container restarts and overkill for this
  cadence. Keep poll for v1.1; revisit if user wants snappier feedback.

If subarr restarts mid-watch, pending rows simply get re-polled on next
boot — no state loss. If subgen restarts, the watcher will observe the
path no longer in the queue and falsely mark it completed (best-effort
behaviour; the worst case is a redundant Bazarr scan-disk-series, which
is harmless).
"""
from __future__ import annotations

import asyncio
import logging

from .integrations import IntegrationError
from .integrations.bazarr import BazarrClient
from .provenance import ProvenanceStore
from .subgen_client import SubgenClient, SubgenUnavailable

log = logging.getLogger(__name__)

WATCHER_INTERVAL_S = 30
# Bazarr task IDs vary across versions. Discovered at runtime by matching
# task labels; we cache the result. Common labels for the per-series scan
# disk are 'sync_episodes', 'update_series', 'scan_disk_series'.
_BAZARR_SCAN_TASK_HINTS = (
    "sync_episodes",
    "scan_disk_series",
    "update_series",
    "sync episodes",
    "scan disk",
)


class CompletionWatcher:
    def __init__(self, subgen: SubgenClient, bazarr: BazarrClient,
                 provenance: ProvenanceStore, interval_s: int = WATCHER_INTERVAL_S):
        self._subgen = subgen
        self._bazarr = bazarr
        self._provenance = provenance
        self._interval_s = interval_s
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._bazarr_task_id: str | None = None
        self._bazarr_task_lookup_attempted = False

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="subarr-completion-watcher")

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
        log.info("completion watcher started (interval=%ds)", self._interval_s)
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception as e:
                log.exception("completion watcher tick failed: %s", e)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_s)
                # If stop was set we exit; otherwise the timeout fires and we loop.
            except asyncio.TimeoutError:
                pass
        log.info("completion watcher stopped")

    async def _tick(self) -> None:
        pending = self._provenance.pending()
        if not pending:
            return
        try:
            q = await self._subgen.queue()
        except SubgenUnavailable as e:
            log.debug("subgen queue unreachable, skipping tick: %s", e)
            return

        in_flight: set[str] = set()
        for t in (q.get("queued") or []) + (q.get("processing") or []):
            if isinstance(t, dict) and t.get("path"):
                in_flight.add(t["path"])

        # Map ledger canonical → subgen container path so we can compare
        # against the in_flight set. Subgen's /queue reports
        # /media/<canonical>; ledger holds the canonical only.
        from .paths import canonical_to_subgen_batch
        for entry in pending:
            subgen_path = canonical_to_subgen_batch(entry.canonical_path)
            if subgen_path in in_flight:
                continue
            # Not in flight any more → consider it done.
            self._provenance.mark_completed(entry.id)
            log.info("completion: %s (ledger #%d)", entry.canonical_path, entry.id)
            if entry.series_id is not None:
                await self._trigger_bazarr_scan(entry.id, entry.series_id)

    async def _trigger_bazarr_scan(self, ledger_id: int, series_id: int) -> None:
        if not self._bazarr.is_configured():
            log.debug("bazarr not configured; skipping scan-disk trigger")
            return
        if self._bazarr_task_id is None and not self._bazarr_task_lookup_attempted:
            await self._discover_bazarr_task()
        if self._bazarr_task_id is None:
            log.warning("no Bazarr scan-disk task id discovered; cannot trigger write-back")
            return
        try:
            await self._bazarr.trigger_task(self._bazarr_task_id)
            self._provenance.mark_bazarr_triggered(ledger_id)
            log.info("bazarr scan-disk triggered for series_id=%d via task %s",
                     series_id, self._bazarr_task_id)
        except IntegrationError as e:
            log.warning("bazarr scan-disk trigger failed: %s", e)

    async def _discover_bazarr_task(self) -> None:
        self._bazarr_task_lookup_attempted = True
        try:
            tasks = await self._bazarr.list_tasks()
        except IntegrationError as e:
            log.warning("bazarr list_tasks failed: %s", e)
            return
        for t in tasks:
            label = (t.get("name") or t.get("job_id") or "").lower()
            for hint in _BAZARR_SCAN_TASK_HINTS:
                if hint in label:
                    self._bazarr_task_id = t.get("job_id") or t.get("id") or t.get("name")
                    log.info("bazarr scan-disk task discovered: %s (matched hint %r)",
                             self._bazarr_task_id, hint)
                    return
        log.info("no bazarr task matched scan-disk hints; available: %s",
                 [t.get("name") for t in tasks][:10])
