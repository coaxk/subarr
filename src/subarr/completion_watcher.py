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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .aftercare import evaluate_subtitle
from .integrations import IntegrationError
from .integrations.bazarr import BazarrClient
from .integrations.plex import PlexClient
from .langs import normalize_lang
from .paths import PathOutsideRootError, canonical_to_fs, library_for_canonical
from .provenance import ProvenanceStore
from .subgen_client import SubgenClient, SubgenUnavailable
from .subtitle_retime import retime_params_from_settings, retime_srt

log = logging.getLogger(__name__)

WATCHER_INTERVAL_S = 30

# #451: bounded advisory text-LID sanity check on the just-completed subtitle.
# Advisory only — NEVER gates completion, upload, scan, or aftercare. The
# blocking py3langid classifier call runs in a dedicated 2-worker thread pool
# under an asyncio semaphore, bounded by LANG_CHECK_TIMEOUT_S. Every failure is
# warning-only (record nothing or a fail-soft status).
_LANG_CHECK_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="textlid")
LANG_CHECK_MAX_CONCURRENCY = 4
LANG_CHECK_TIMEOUT_S = 2.0
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
        # #451: retained in-flight advisory text-LID checks keyed by canonical
        # subtitle identity (frozenset of identity items). Holds a strong ref
        # (GC-safe, mirroring _forced_segment_tasks) and coalesces duplicate
        # schedules (one in-flight check per identity). Bounded by an asyncio
        # semaphore (built lazily so __new__-constructed test watchers work).
        self._lang_check_tasks: dict = {}
        self._lang_check_semaphore: asyncio.Semaphore | None = None

    @property
    def _subgen(self):
        return self._subgen_provider()

    @property
    def _bazarr(self):
        return self._bundle_provider().bazarr if self._bundle_provider else self._bazarr_direct

    @property
    def _plex(self):
        return self._bundle_provider().plex if self._bundle_provider else self._plex_direct

    @property
    def _media_servers(self):
        if self._bundle_provider:
            return self._bundle_provider().media_servers
        return [self._plex_direct] if self._plex_direct else []

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
            except asyncio.CancelledError:
                pass  # normal supervisor shutdown
            except Exception:  # stop() must never raise; teardown is best-effort
                log.debug("watcher loop task ended with an error during stop", exc_info=True)
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
                log.exception("completion watcher tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_s)
                # If stop was set we exit; otherwise the timeout fires and we loop.
            except TimeoutError:
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
        """#71: best-effort media-server refresh trigger, fanned out over every
        CONFIGURED media server (Plex today; Jellyfin/Emby join via
        media_servers in later slices). Each server sees the file at its own
        mount path (translated internally by that server's client); we pass
        the resolved subarr-side full path and the fan-out handles the rest.

        Disabled cleanly when (a) no media server wired/configured, (b)
        PLEX_PARTIAL_SCAN_ENABLED=0. A single failing server never blocks the
        others (refresh_file_on_all is best-effort) and never raises into the
        completion loop."""
        servers = [s for s in self._media_servers if s and s.is_configured()]
        if not servers:
            return
        from .config import settings as _settings

        if not _settings.plex_partial_scan_enabled:
            return
        try:
            # #134: library-aware resolve (@slug/ heads).
            subarr_full = str(canonical_to_fs(video_canonical))
        except PathOutsideRootError:
            log.warning("media-server refresh skipped: unresolvable canonical %s", video_canonical)
            return
        from .integrations.media_server import refresh_file_on_all

        results = await refresh_file_on_all(servers, subarr_full)
        for r in results:
            log.info("media-server refresh fired: %s (ledger entry: %s)", r, video_canonical)

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
            # #451: once the produced subtitle is available, schedule the
            # bounded advisory text-LID check. Best-effort + warning-only; it
            # records its result on the row just written above.
            self._schedule_language_check(entry, srt_path)
        except Exception as e:  # noqa: BLE001 - aftercare must never break completion
            log.warning("aftercare judging failed for %s: %s", getattr(entry, "canonical_path", "?"), e)

    # ------------------------------------------------------------------
    # #451: bounded advisory text-LID sanity check (warning-only, fail-soft)
    # ------------------------------------------------------------------

    def _schedule_language_check(self, entry, srt_path: str) -> None:
        """#451: schedule a bounded, advisory text-LID sanity check once the
        produced subtitle is available. Warning-only: it NEVER raises, NEVER
        blocks completion/upload/scan/aftercare, and records nothing on failure.

        Uses a RETAINED `asyncio.create_task` (GC-safe strong ref), duplicate
        coalescing by canonical subtitle identity (one in-flight check per
        identity), an asyncio semaphore for bounded concurrency, a
        `ThreadPoolExecutor(max_workers=2)` for the blocking classifier call,
        and `asyncio.wait_for(timeout=2.0)`. Cancellation/timeout/exception/
        missing sidecar/backend-unavailable are all advisory-only."""
        try:
            import asyncio

            from .text_lid import canonical_subtitle_identity

            identity = canonical_subtitle_identity(
                video_path=entry.canonical_path,
                subtitle_path=srt_path,
                # The .srt's declared output language = the ledger's declared
                # target, normalized. NEVER a filename, the hardcoded upload
                # language, provider, OCR, or HI preference. NULL when unknown
                # -> unknown provenance (checker returns INCONCLUSIVE).
                subtitle_language=normalize_lang(getattr(entry, "target_language", None)),
                ledger_id=getattr(entry, "id", 0),
            )
        except Exception as e:  # noqa: BLE001 - advisory identity build must not raise
            log.warning("text-lid: identity build failed for %s: %s", srt_path, e)
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return  # no running loop (unit context / non-loop driver) — advisory, skip
        if getattr(self, "_lang_check_tasks", None) is None:
            self._lang_check_tasks = {}
        key = frozenset(identity.items())
        if key in self._lang_check_tasks:
            return  # already in flight for this canonical identity — coalesce
        sem = self._lang_semaphore()
        try:
            t = asyncio.create_task(
                self._lang_check_worker(entry, srt_path, identity, key, sem),
                name="subarr-text-lid-check",
            )
        except RuntimeError as e:
            log.warning("text-lid: no running loop to schedule %s: %s", srt_path, e)
            return
        self._lang_check_tasks[key] = t
        t.add_done_callback(lambda _t: self._lang_check_tasks.pop(key, None))

    def _lang_semaphore(self) -> asyncio.Semaphore:
        """Lazily-created asyncio semaphore bounding concurrent language checks.
        Built on the running loop so __new__-constructed test watchers work."""
        import asyncio

        sem = getattr(self, "_lang_check_semaphore", None)
        if sem is None:
            sem = asyncio.Semaphore(LANG_CHECK_MAX_CONCURRENCY)
            self._lang_check_semaphore = sem
        return sem

    async def _lang_check_worker(self, entry, srt_path, identity, key, sem) -> None:
        """Advisory background worker. Bounded by the semaphore + wait_for
        timeout. Cancellation propagates (supervisor shutdown); every other
        failure is logged warning-only and records nothing."""
        import asyncio

        try:
            async with sem:
                await self._run_language_check(entry, srt_path, identity)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - advisory must never break the loop
            log.warning("text-lid: advisory check failed for %s: %s", srt_path, e)

    async def _run_language_check(self, entry, srt_path, identity) -> None:
        """Read the produced subtitle, run the bounded checker in the thread
        pool under a timeout, and record ONE bounded result. Missing sidecar,
        timeout, cancellation, and exceptions all fail soft (record nothing)."""
        import asyncio
        import hashlib

        from .text_lid import check_subtitle_text

        try:
            text_bytes = Path(srt_path).read_bytes()
        except OSError as e:
            log.warning("text-lid: sidecar read failed for %s: %s", srt_path, e)
            return  # missing/unreadable sidecar -> fail-soft, record nothing
        content_sha256 = hashlib.sha256(text_bytes).hexdigest()
        # Explicit ledger/provenance context (P5-S2). expected_languages is left
        # to the checker to derive from the declared task/source/target contract
        # — never a filename, hardcoded upload language, provider, OCR, or HI
        # preference. Unknown provenance naturally yields INCONCLUSIVE.
        kwargs = dict(
            canonical_identity=identity,
            content_sha256=content_sha256,
            expected_languages=[],
            task=getattr(entry, "task", None),
            source_language=getattr(entry, "source_language", None),
            target_language=getattr(entry, "target_language", None),
            submission_origin=getattr(entry, "submission_origin", None),
            webhook_event=getattr(entry, "webhook_event", None),
            webhook_language=getattr(entry, "webhook_language", None),
            webhook_subtitle=getattr(entry, "webhook_subtitle", None),
            provenance_conflict=getattr(entry, "provenance_conflict", None),
        )
        try:
            result = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    _LANG_CHECK_EXECUTOR, lambda: check_subtitle_text(text_bytes, **kwargs)
                ),
                timeout=LANG_CHECK_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            log.warning("text-lid: advisory check timed out for %s", srt_path)
            return  # timeout -> fail-soft, record nothing
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - inference failure is advisory
            log.warning("text-lid: advisory check errored for %s: %s", srt_path, e)
            return  # exception -> fail-soft, record nothing
        self._record_lang_check(entry, result)

    def _record_lang_check(self, entry, result) -> None:
        """Persist ONE bounded structured result onto the latest aftercare row
        for this path. Best-effort and advisory — a store failure only logs.
        Persists the bounded result only (status/reason/provenance/versions/…),
        never full subtitle text."""
        store = getattr(self, "_aftercare", None)
        if store is None or not hasattr(store, "set_text_lang_check"):
            return
        try:
            store.set_text_lang_check(entry.canonical_path, result.to_dict())
        except Exception as e:  # noqa: BLE001 - advisory store write must not raise
            log.warning("text-lid: failed to record advisory result for %s: %s", entry.canonical_path, e)

    def _run_retime(self, entry) -> None:
        """#359: re-time the produced .srt in place (extend over-CPS cues into
        the gap before the next cue) BEFORE aftercare + upload, so both see the
        improved sub. On by default; opt out with SUBARR_RETIME_ENABLED=0.
        Best-effort — a failure here must NEVER block completion. Writes only if
        changed. The retimer runs with the ACTIVE tuning config (P2-S2): the five
        numeric RetimeParams come from the running Settings singleton
        (target_cps/min_cue_ms/min_gap_ms/max_cue_ms/max_borrow_ms), so a live or
        persisted tuning change applies on the next completion without a restart.
        RetimeParams' own defaults are never mutated — they are only overridden
        by explicit values when Settings carries them."""
        from .config import settings as _settings

        if not _settings.retime_enabled:
            return
        try:
            srt_path = self._find_srt_sidecar(entry.canonical_path)
            if not srt_path:
                return
            text = Path(srt_path).read_text(encoding="utf-8", errors="replace")
            params = retime_params_from_settings(_settings)
            new_text = retime_srt(text, params)
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
