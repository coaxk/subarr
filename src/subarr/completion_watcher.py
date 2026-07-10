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
import time
from pathlib import Path

from .aftercare import evaluate_subtitle
from .integrations import IntegrationError
from .paths import PathOutsideRootError, canonical_to_fs, library_for_canonical
from .integrations.bazarr import BazarrClient
from .integrations.plex import PlexClient
from .provenance import ProvenanceStore
from .subgen_client import SubgenClient, SubgenUnavailable
from .subtitle_retime import retime_srt

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
    "series_full_scan_subtitles",  # Bazarr 1.5.x — episodes
    "movies_full_scan_subtitles",  # Bazarr 1.5.x — movies (we mostly do episodes)
    "scan_disk_series",  # forward-compat
    "scan_disk_episodes",
    "scan disk",  # human-readable fallback
    "index all existing episodes",  # human-readable name match
)


class CompletionWatcher:
    def __init__(
        self,
        subgen: SubgenClient | None = None,
        bazarr: BazarrClient | None = None,
        provenance: ProvenanceStore = None,
        interval_s: int = WATCHER_INTERVAL_S,
        caps_provider=None,
        plex: PlexClient | None = None,
        bundle_provider=None,
        subgen_provider=None,
        aftercare_store=None,
        duration_lookup=None,
    ):
        # Clients are resolved live so onboarding can swap them on
        # app.state without restarting the watcher. When a bundle_provider
        # is given, bazarr + plex come from the live bundle; otherwise the
        # directly-passed clients are used (backward compatible).
        self._bundle_provider = bundle_provider
        self._bazarr_direct = bazarr
        self._plex_direct = plex
        self._subgen_provider = subgen_provider or (lambda: subgen)
        self._provenance = provenance
        self._interval_s = interval_s
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        # #364: strong refs to in-flight at-import forced-segment scans. CPython
        # keeps only a WEAK ref to a bare create_task, so a long scan (VAD ->
        # per-utterance LID -> translate) can be GC-cancelled mid-flight and the
        # .forced.en.srt silently never lands. Hold each task here; the
        # done-callback discards it (see _maybe_forced_segment).
        self._forced_segment_tasks: set = set()
        # #161 P3: scan-disk task id cached PER Bazarr instance (key = base_url).
        self._bazarr_task_ids: dict[str, str] = {}
        # Cached caps. When /queue is missing (vanilla subgen), the
        # _pass_pending queue-poll is skipped + a one-time warning logs.
        # v1.x will add a file-watch fallback that detects .srt sidecars
        # landing on disk so provenance entries can still auto-complete.
        self._caps_provider = caps_provider or (lambda: None)
        self._warned_no_queue = False
        self._aftercare = aftercare_store
        # #216: canonical_path -> media duration_s (or None), resolved from
        # the ffprobe cache. Enables aftercare's sync-overrun signal.
        self._duration_lookup = duration_lookup or (lambda canonical: None)

    @property
    def _subgen(self):
        return self._subgen_provider()

    @property
    def _bazarr(self):
        return self._bundle_provider().bazarr if self._bundle_provider else self._bazarr_direct

    @property
    def _plex(self):
        return self._bundle_provider().plex if self._bundle_provider else self._plex_direct

    def _bazarr_for(self, canonical_path: str):
        """#161 P3: the Bazarr client for the instance that owns this row's
        library (instance 0 when single-stack / unbound). Falls back to the
        directly-injected client when there is no bundle provider."""
        if self._bundle_provider is None:
            return self._bazarr_direct
        bundle = self._bundle_provider()
        return bundle.client_for("bazarr", library_for_canonical(canonical_path).bazarr_id)

    async def _bazarr_task_for(self, bz) -> str | None:
        """Discover + cache the Bazarr scan-disk task id for THIS instance
        (keyed by base_url; cached on success only, so a transient failure can
        retry next tick)."""
        key = getattr(bz, "_base_url", "") or str(id(bz))
        cached = self._bazarr_task_ids.get(key)
        if cached:
            return cached
        try:
            tasks = await bz.list_tasks()
        except IntegrationError as e:
            log.warning("bazarr list_tasks failed: %s", e)
            return None
        for hint in _BAZARR_SCAN_TASK_HINTS:
            h = hint.lower()
            for t in tasks:
                job_id = (t.get("job_id") or "").lower()
                name = (t.get("name") or "").lower()
                if h in job_id or h in name:
                    tid = t.get("job_id") or t.get("id") or t.get("name")
                    self._bazarr_task_ids[key] = tid
                    log.info("bazarr scan-disk task for %s: %s (hint %r)", key, tid, hint)
                    return tid
        log.warning(
            "no bazarr scan-disk task matched hints for %s; job_ids: %s",
            key,
            [t.get("job_id") for t in tasks][:20],
        )
        return None

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
        # #364: best-effort cancel any in-flight at-import forced-segment scans
        # so a shutdown never leaves them dangling. Don't await (stop() does not
        # await other tasks); the done-callback empties the set as they unwind.
        for t in list(self._forced_segment_tasks):
            t.cancel()

    async def _loop(self) -> None:
        log.info("completion watcher started (interval=%ds)", self._interval_s)
        while not self._stop.is_set():
            try:
                await self._tick()
                _h = getattr(self, "_health", None)  # #157 supervision hook
                if _h:
                    _h.record_success("completion-watcher", expected_interval_s=self._interval_s)
            except Exception as e:
                _h = getattr(self, "_health", None)
                if _h:
                    _h.record_failure("completion-watcher", e, expected_interval_s=self._interval_s)
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
            await self.complete_entry(entry)

    async def complete_entry(self, entry) -> None:
        """Run the full completion flow for one ledger entry: mark it
        completed, write the .srt back to Bazarr (direct upload, falling
        back to a scan-disk trigger), and fire a Plex partial-scan.

        This is the single battle-tested completion path. It is invoked
        both by the polling pass (_pass_pending) and by the push-based
        webhook receiver (#87). Idempotent enough for either driver — a
        second call merely re-stamps completed_at and re-fires harmless
        best-effort write-backs.
        """
        self._provenance.mark_completed(entry.id)
        self._run_retime(entry)
        self._run_aftercare(entry)
        self._maybe_forced_segment(entry)  # #364: best-effort background deep-scan (never blocks)
        log.info("completion: %s (ledger #%d)", entry.canonical_path, entry.id)
        # v1.1-G: try direct multipart upload first (closes the loop
        # tightly + no race vs. Bazarr's filesystem scan). Falls back
        # to scan-disk task if upload fails or we lack episode_id.
        uploaded = await self._try_upload_to_bazarr(entry)
        if not uploaded and entry.series_id is not None:
            await self._trigger_bazarr_scan(entry.id, entry.series_id, entry.canonical_path)
        # v1.1.1: fire Plex partial-scan against the file's directory
        # so the freshly-written sidecar appears in Plex (and on Apple
        # TV) without waiting for Plex's periodic scan. Best-effort —
        # failure here doesn't unwind anything; Plex will pick it up
        # on the next periodic scan anyway.
        await self._maybe_plex_partial_scan(entry.canonical_path)

    async def complete_by_canonical(self, canonical_path: str) -> int:
        """Push entrypoint (#87): given a canonical path that subgen just
        reported finished via WEBHOOK_URL_COMPLETED, run the completion
        flow for every matching still-pending ledger entry.

        Returns the number of entries completed. Zero is normal and benign
        — it just means the polling pass already handled this path, or the
        path was never tracked by subarr (e.g. a subgen Plex/Tautulli
        auto-transcribe rather than a subarr-submitted job)."""
        matches = [e for e in self._provenance.query_by_path(canonical_path) if e.completed_at is None]
        for entry in matches:
            await self.complete_entry(entry)
        return len(matches)

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
        # #161 P3: one library-wide trigger PER Bazarr instance. Bazarr's
        # series_full_scan_subtitles is library-wide, but with multiple stacks a
        # row's scan must fire on the Bazarr that owns its library — so group the
        # stuck rows by owning instance and fire each once.
        by_instance: dict[str, list] = {}
        clients: dict[str, object] = {}
        for entry in stuck:
            if entry.series_id is None:
                continue
            bz = self._bazarr_for(entry.canonical_path)
            key = getattr(bz, "_base_url", "") or str(id(bz))
            clients.setdefault(key, bz)
            by_instance.setdefault(key, []).append(entry)
        for key, entries in by_instance.items():
            # #372: isolate each instance. The task-discovery call (_bazarr_task_for)
            # only catches IntegrationError, so a non-IntegrationError from one
            # Bazarr's list_tasks would otherwise abort the pass for every remaining
            # instance. Mirror the scheduler's per-instance broad-except fan-out.
            try:
                bz = clients[key]
                if not bz.is_configured():
                    continue
                tid = await self._bazarr_task_for(bz)
                if tid is None:
                    continue
                await bz.trigger_task(tid)
                log.info(
                    "bazarr scan-disk retry fired on %s for %d completed-but-not-notified rows",
                    key,
                    len(entries),
                )
                # Mark each row notified so we don't keep re-firing.
                for entry in entries:
                    self._provenance.mark_bazarr_triggered(entry.id)
            except Exception as e:  # noqa: BLE001 - one instance's failure must not abort the rest
                log.warning("bazarr scan-disk retry failed on %s: %s", key, e)
                continue

    async def _try_upload_to_bazarr(self, entry) -> bool:
        """v1.1-G: Multipart-upload the freshly-Whispered .srt directly
        to Bazarr. Returns True on success (skip scan-disk), False on any
        miss so the caller falls through to the legacy scan-disk trigger.

        Why both paths? Bazarr's upload endpoint was added in 1.4 and is
        the cleanest write-back — no race vs filesystem scans. But older
        Bazarrs or movie rows without a known radarr_id need the disk-scan
        fallback. Best-effort: any error → fallback path."""
        bz = self._bazarr_for(entry.canonical_path)
        if not bz.is_configured():
            return False
        srt_path = self._find_srt_sidecar(entry.canonical_path)
        if srt_path is None:
            log.debug("upload: no .srt sidecar found for %s", entry.canonical_path)
            return False
        try:
            if entry.sonarr_episode_id and entry.series_id:
                await bz.upload_episode_subtitle(
                    series_id=entry.series_id,
                    episode_id=entry.sonarr_episode_id,
                    language="en",
                    file_path=srt_path,
                )
                self._provenance.mark_bazarr_triggered(entry.id)
                log.info("bazarr upload OK for episode %d (ledger #%d)", entry.sonarr_episode_id, entry.id)
                return True
            # #368: movie path — same direct upload, keyed by radarr_movie_id
            # (now carried on the job → provenance). Routes to the owning Bazarr
            # via _bazarr_for above. No radarr id → fall through to scan-disk.
            if entry.radarr_movie_id:
                await bz.upload_movie_subtitle(
                    radarr_id=entry.radarr_movie_id,
                    language="en",
                    file_path=srt_path,
                )
                self._provenance.mark_bazarr_triggered(entry.id)
                log.info("bazarr upload OK for movie %d (ledger #%d)", entry.radarr_movie_id, entry.id)
                return True
            return False
        except IntegrationError as e:
            log.warning("bazarr upload failed (%s); falling back to scan-disk", e)
            return False
        except OSError as e:
            log.warning("bazarr upload skipped (.srt read error: %s); fallback", e)
            return False

    async def _maybe_plex_partial_scan(self, video_canonical: str) -> None:
        """v1.1.1: best-effort Plex partial-scan trigger. Plex sees the file
        at its own mount path (translated via PLEX_PATH_PREFIX if set); we
        pass the resolved subarr-side full path to the client which handles
        translation + section discovery.

        Disabled cleanly when (a) no plex client wired, (b) PLEX_PARTIAL_SCAN_ENABLED=0,
        (c) Plex unconfigured. All failures log.warning and return — never
        raises into the completion loop."""
        if self._plex is None:
            return
        from .config import settings as _settings

        if not _settings.plex_partial_scan_enabled:
            return
        if not self._plex.is_configured():
            return
        try:
            # #134: library-aware resolve (@slug/ heads).
            subarr_full = str(canonical_to_fs(video_canonical))
        except PathOutsideRootError:
            log.warning("plex partial-scan skipped: unresolvable canonical %s", video_canonical)
            return
        try:
            result = await self._plex.partial_scan(subarr_full)
            log.info(
                "plex partial-scan fired: section=%s path=%s (ledger entry: %s)",
                result.get("section"),
                result.get("plex_path"),
                video_canonical,
            )
        except IntegrationError as e:
            log.warning(
                "plex partial-scan failed for %s: %s (will be picked up by Plex's next periodic scan)",
                video_canonical,
                e,
            )

    def _run_aftercare(self, entry) -> None:
        """#156: judge the produced subtitle and record the result. Best-effort
        - a failure here must NEVER block completion / the loop."""
        if not getattr(self, "_aftercare", None):
            return
        try:
            srt_path = self._find_srt_sidecar(entry.canonical_path)
            if not srt_path:
                return
            text = Path(srt_path).read_text(encoding="utf-8", errors="replace")
            try:
                duration_s = self._duration_lookup(entry.canonical_path)
            except Exception:  # noqa: BLE001 - probe lookup must not block judging
                duration_s = None
            ev = evaluate_subtitle(text, media_duration_s=duration_s)
            self._aftercare.record(
                canonical_path=entry.canonical_path,
                completed_at=time.time(),
                evaluation=ev,
                source=getattr(entry, "source", None) or "subgenscan",
            )
        except Exception as e:  # noqa: BLE001 - aftercare must never break completion
            log.warning("aftercare judging failed for %s: %s", getattr(entry, "canonical_path", "?"), e)

    def _run_retime(self, entry) -> None:
        """#359: re-time the produced .srt in place (extend over-CPS cues into
        the gap before the next cue) BEFORE aftercare + upload, so both see the
        improved sub. On by default; opt out with SUBARR_RETIME_ENABLED=0.
        Best-effort — a failure here must NEVER block completion. Writes only if
        changed."""
        from .config import settings as _settings

        if not _settings.retime_enabled:
            return
        try:
            srt_path = self._find_srt_sidecar(entry.canonical_path)
            if not srt_path:
                return
            text = Path(srt_path).read_text(encoding="utf-8", errors="replace")
            new_text = retime_srt(text)
            if new_text != text:
                Path(srt_path).write_text(new_text, encoding="utf-8")
                log.info("re-timed %s", entry.canonical_path)
        except Exception as e:  # noqa: BLE001 - re-timing must never break completion
            log.warning("re-time failed for %s: %s", getattr(entry, "canonical_path", "?"), e)

    def _maybe_forced_segment(self, entry) -> None:
        """#364: if the feature is enabled and a generator is wired, schedule a
        BACKGROUND forced-segment scan for this just-completed file. The
        generator re-checks the gate + scan cache internally, so this hook only
        schedules — it NEVER blocks completion and never raises. Best-effort:
        LOG the reason on any miss (the #416 lesson — don't swallow silently)."""
        runner = getattr(self, "_forced_segment", None)
        if runner is None:
            return
        from .config import settings as _settings

        if not _settings.forced_segment_enabled:
            return
        try:
            import asyncio

            # Retain a strong ref (GC-safe) and release it on completion so a
            # long at-import scan can never be silently cancelled mid-flight.
            t = asyncio.create_task(self._forced_segment_bg(entry.canonical_path))
            self._forced_segment_tasks.add(t)
            t.add_done_callback(self._forced_segment_tasks.discard)
        except RuntimeError as e:
            log.warning("forced-segment at-import: no running loop for %s: %s", entry.canonical_path, e)

    async def _forced_segment_bg(self, canonical_path: str) -> None:
        try:
            await self._forced_segment.process(canonical_path)
        except Exception as e:  # noqa: BLE001 - at-import scan must never break completion
            log.warning("forced-segment at-import scan failed for %s: %s", canonical_path, e)

    def _find_srt_sidecar(self, video_canonical: str) -> str | None:
        """Locate the .srt subgen wrote next to the video. Subgen's default
        naming is <basename>.en.srt; fall back to any .srt sharing the
        basename if the language tag differs."""
        try:
            # #134: library-aware resolve (@slug/ heads).
            full = canonical_to_fs(video_canonical)
            if not full.exists():
                return None
        except (OSError, PathOutsideRootError):
            return None
        stem = full.stem
        parent = full.parent
        # Preferred: <stem>.en.srt
        candidate = parent / f"{stem}.en.srt"
        if candidate.exists():
            return str(candidate)
        # Fallback: any sibling .srt sharing the stem
        try:
            for p in parent.glob(f"{stem}*.srt"):
                return str(p)
        except OSError:
            pass
        return None

    async def _trigger_bazarr_scan(self, ledger_id: int, series_id: int, canonical_path: str) -> None:
        bz = self._bazarr_for(canonical_path)
        if not bz.is_configured():
            log.debug("bazarr not configured; skipping scan-disk trigger")
            return
        tid = await self._bazarr_task_for(bz)
        if tid is None:
            log.warning("no Bazarr scan-disk task id discovered; cannot trigger write-back")
            return
        try:
            await bz.trigger_task(tid)
            self._provenance.mark_bazarr_triggered(ledger_id)
            log.info("bazarr scan-disk triggered for series_id=%d via task %s", series_id, tid)
        except IntegrationError as e:
            log.warning("bazarr scan-disk trigger failed: %s", e)

    # _discover_bazarr_task removed (#161 P3): replaced by per-instance
    # _bazarr_task_for(bz), which caches the scan-disk task id keyed by instance.
