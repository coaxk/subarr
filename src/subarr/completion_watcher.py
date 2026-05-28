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
# both job_id AND name fields. Hint order = priority: first match wins.
#
# Verified against live Bazarr 1.5.6 (2026-05-28):
#   series_full_scan_subtitles → "Index All Existing Episodes Subtitles"
#     ↑ THE RIGHT TASK — disk scan, picks up new .srt files we just wrote
#   update_series → "Sync with Sonarr" (metadata only, NOT disk scan — wrong)
#   wanted_search_missing_subtitles_series → searches providers, not disk
#
# Earlier subarr versions used a hint list that prioritised the wrong tasks
# (update_series / sync_episodes); the watcher silently never triggered
# Bazarr because the actual scan-disk task name is `series_full_scan_subtitles`.
_BAZARR_SCAN_TASK_HINTS = (
    "series_full_scan_subtitles",   # Bazarr 1.5.x — episodes
    "movies_full_scan_subtitles",   # Bazarr 1.5.x — movies (we mostly do episodes)
    "scan_disk_series",             # forward-compat
    "scan_disk_episodes",
    "scan disk",                    # human-readable fallback
    "index all existing episodes",  # human-readable name match
)


class CompletionWatcher:
    def __init__(self, subgen: SubgenClient, bazarr: BazarrClient,
                 provenance: ProvenanceStore, interval_s: int = WATCHER_INTERVAL_S,
                 caps_provider=None):
        self._subgen = subgen
        self._bazarr = bazarr
        self._provenance = provenance
        self._interval_s = interval_s
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._bazarr_task_id: str | None = None
        self._bazarr_task_lookup_attempted = False
        # Cached caps. When /queue is missing (vanilla subgen), the
        # _pass_pending queue-poll is skipped + a one-time warning logs.
        # v1.x will add a file-watch fallback that detects .srt sidecars
        # landing on disk so provenance entries can still auto-complete.
        self._caps_provider = caps_provider or (lambda: None)
        self._warned_no_queue = False

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
        # Two passes per tick:
        #   1. Pending entries → check subgen queue, mark complete + trigger Bazarr
        #   2. Completed-but-not-notified entries → retry Bazarr trigger.
        #      Covers the case where a prior subarr version had the wrong
        #      task hints (or Bazarr was down) and the trigger silently
        #      no-op'd. Self-healing.
        await self._pass_pending()
        await self._pass_retry_bazarr_notify()

    async def _pass_pending(self) -> None:
        pending = self._provenance.pending()
        if not pending:
            return
        # Compat-mode check: if subgen lacks /queue, this pass can't work.
        # Log once + bail; v1.x adds the file-watch fallback.
        caps = self._caps_provider()
        if caps is not None and caps.reachable and not caps.has_queue:
            if not self._warned_no_queue:
                log.warning(
                    "completion watcher: subgen has no /queue endpoint "
                    "(compat mode — vanilla subgen). %d pending provenance "
                    "entries won't auto-complete via queue polling. "
                    "File-watch fallback is v1.x; for now, restart subgen "
                    "or use subarr-subgen image for auto-completion.",
                    len(pending),
                )
                self._warned_no_queue = True
            return
        try:
            q = await self._subgen.queue()
        except SubgenUnavailable as e:
            log.debug("subgen queue unreachable, skipping pending pass: %s", e)
            return

        in_flight: set[str] = set()
        for t in (q.get("queued") or []) + (q.get("processing") or []):
            if isinstance(t, dict) and t.get("path"):
                in_flight.add(t["path"])

        from .paths import canonical_to_subgen_batch
        for entry in pending:
            subgen_path = canonical_to_subgen_batch(entry.canonical_path)
            if subgen_path in in_flight:
                continue
            self._provenance.mark_completed(entry.id)
            log.info("completion: %s (ledger #%d)", entry.canonical_path, entry.id)
            if entry.series_id is not None:
                await self._trigger_bazarr_scan(entry.id, entry.series_id)

    async def _pass_retry_bazarr_notify(self) -> None:
        """Find ledger entries that completed but never successfully fired
        Bazarr's scan-disk task — e.g. because the task hint list was wrong
        in a previous subarr version, or Bazarr was down at the time. Retry
        the trigger so Bazarr eventually learns about the .srt and stops
        listing the episode as wanted.

        Bounded retry: only entries completed within the last 24h to avoid
        retrying ancient rows after a long downtime. Single Bazarr task
        trigger per tick is enough — the task scans the whole library."""
        try:
            stuck = self._provenance.completed_without_bazarr(max_age_s=86400)
        except AttributeError:
            return  # older provenance store without this method
        if not stuck:
            return
        # We only need ONE trigger to flush all of them — Bazarr's
        # series_full_scan_subtitles is library-wide.
        fired = False
        for entry in stuck:
            if entry.series_id is None:
                continue
            if not fired:
                if not self._bazarr.is_configured():
                    return
                if self._bazarr_task_id is None:
                    await self._discover_bazarr_task()
                if self._bazarr_task_id is None:
                    return
                try:
                    await self._bazarr.trigger_task(self._bazarr_task_id)
                    log.info(
                        "bazarr scan-disk retry fired for %d completed-but-not-notified rows",
                        len(stuck),
                    )
                    fired = True
                except IntegrationError as e:
                    log.warning("bazarr scan-disk retry failed: %s", e)
                    return
            # Mark each row notified so we don't keep re-firing.
            self._provenance.mark_bazarr_triggered(entry.id)

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
        # Allow re-discovery if previous attempts failed — Bazarr might have
        # been transiently down. We only flip _bazarr_task_lookup_attempted
        # to True on SUCCESS so a one-off failure doesn't lock us out.
        try:
            tasks = await self._bazarr.list_tasks()
        except IntegrationError as e:
            log.warning("bazarr list_tasks failed: %s", e)
            return
        # Check both job_id AND name against each hint — hints can match
        # either canonical IDs (`series_full_scan_subtitles`) or human
        # labels ("Index All Existing Episodes Subtitles").
        for hint in _BAZARR_SCAN_TASK_HINTS:
            h = hint.lower()
            for t in tasks:
                job_id = (t.get("job_id") or "").lower()
                name = (t.get("name") or "").lower()
                if h in job_id or h in name:
                    self._bazarr_task_id = t.get("job_id") or t.get("id") or t.get("name")
                    self._bazarr_task_lookup_attempted = True
                    log.info(
                        "bazarr scan-disk task discovered: %s (matched hint %r against %s)",
                        self._bazarr_task_id, hint,
                        "job_id" if h in job_id else "name",
                    )
                    return
        log.warning(
            "no bazarr task matched scan-disk hints %s; available job_ids: %s",
            list(_BAZARR_SCAN_TASK_HINTS), [t.get("job_id") for t in tasks][:20],
        )
