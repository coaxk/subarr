"""Sequential scan executor.

A ScanRunner owns one asyncio.Task per active scan; tasks pull paths from the
Scan record, call subgen /batch sequentially, and emit events through a
per-scan asyncio.Queue that the SSE endpoint subscribes to.

Sequential because subgen is single-GPU; parallel /batch calls just queue up
inside subgen anyway. Sequential here = honest UI.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator

from .paths import canonical_to_subgen_batch
from .scan_store import (
    PATH_STATUS_EMPTY,
    PATH_STATUS_ERROR,
    PATH_STATUS_OK,
    PATH_STATUS_RUNNING,
    PATH_STATUS_SKIPPED,
    SCAN_STATUS_DONE,
    SCAN_STATUS_ERROR,
    SCAN_STATUS_RUNNING,
    Scan,
    ScanStore,
)
from .subgen_client import SubgenCapabilities, SubgenClient, SubgenUnavailable

log = logging.getLogger(__name__)


def classify_batch_outcome(status_code: int, body) -> "tuple[str, str]":
    """Map a subgen POST /batch (status_code, body) to (path_status, error).

    A non-200 response carries no `walked` field, so the old `walked == 0`
    catch-all mislabeled auth/permission failures (401/403) and server errors
    (500) as "file removed / no longer on disk". EMPTY now requires a genuine
    200-walked-nothing (or a 404 for the path); 401/403 surface as an auth error
    that tells the user subgen rejected the write; any other non-200 is an error.
    """
    walked = body.get("walked", 0) if isinstance(body, dict) else 0
    queued = body.get("queued", 0) if isinstance(body, dict) else 0
    skipped = body.get("skipped", 0) if isinstance(body, dict) else 0
    already_q = body.get("already_in_queue", 0) if isinstance(body, dict) else 0
    if status_code == 200 and walked > 0 and queued > 0:
        return PATH_STATUS_OK, ""
    if status_code == 200 and walked > 0 and queued == 0 and skipped > 0:
        # Subgen walked but skipped everything (target sub exists, audio-lang
        # match, or another should_skip branch). NOT an error; /batch gives a
        # single counter without per-file reasons (queue.py infers where it can).
        reasons = []
        if skipped:
            reasons.append(
                f"subgen skipped {skipped} — reason not in /batch response "
                f"(target sub exists, audio-lang match, or other skip rule). "
                f"See subgen logs for the per-file reason; queue.py infers "
                f"sub_exists vs audio_lang where possible."
            )
        if already_q:
            reasons.append(f"{already_q} already in queue")
        if body.get("no_audio", 0):
            reasons.append(f"{body.get('no_audio', 0)} no audio")
        if body.get("pending_language_detect", 0):
            reasons.append(f"{body.get('pending_language_detect', 0)} pending lang detect")
        return PATH_STATUS_SKIPPED, ("; ".join(reasons) if reasons else "skipped")
    if status_code == 200 and walked > 0 and queued == 0 and already_q > 0:
        return PATH_STATUS_OK, ""  # already in subgen's queue — it'll process
    if status_code in (401, 403):
        return PATH_STATUS_ERROR, (
            f"subgen rejected POST /batch ({status_code}) — check subgen auth / API key, "
            f"or a reverse proxy in front of subgen blocking write requests"
        )
    if status_code == 404 or (status_code == 200 and walked == 0):
        return PATH_STATUS_EMPTY, ""  # subgen walked and found nothing = file gone
    return PATH_STATUS_ERROR, f"unexpected subgen response: {status_code}"


class CompatModeError(RuntimeError):
    """The detected subgen build doesn't support a capability subarr's
    scan flow needs. Surfaces a clear UI message instead of a vague
    HTTP error or silent no-op."""


class ScanRunner:
    def __init__(
        self,
        subgen: SubgenClient | None = None,
        store: ScanStore = None,
        caps_provider=None,
        subgen_provider=None,
        error_recorder=None,
    ):
        # subgen is resolved through a provider so onboarding live-reload
        # can swap the client on app.state without restarting the runner.
        # Backward compatible: a directly-passed `subgen` is wrapped in a
        # constant provider.
        self._subgen_provider = subgen_provider or (lambda: subgen)
        self._store = store
        self._tasks: dict[str, asyncio.Task] = {}
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        # v1.1.1 #224: per-scan audio_language_override (ISO 639-1 or
        # LanguageCode-parseable). Set at start() when the caller has a
        # user-verified ground-truth language for the file. Forwarded to
        # subgen's POST /batch so it bypasses SKIP_IF_AUDIO_LANGUAGES.
        # Lives in memory only — if subarr restarts mid-scan the override
        # is lost, but so is the in-flight scan, so no consistency hole.
        self._overrides: dict[str, str] = {}
        # #317 Slice B: scan ids that should pass ?ignore_forced=true to subgen's
        # /batch (transcribe a forced-only file anyway). In-memory, same lifetime
        # as _overrides — lost on restart, but so is the in-flight scan.
        self._ignore_forced: set[str] = set()
        # [#458 follow-on] scans allowed to overrule subgen's skip heuristics
        # entirely -- the image-only-subtitle class, which SKIP_IF_AUDIO_LANGUAGES
        # otherwise refuses forever.
        self._bypass_skip: set[str] = set()
        self._lock = asyncio.Lock()
        # Callable returning the cached SubgenCapabilities snapshot.
        # We don't probe per-scan — caps are stable per app boot.
        # The provider may return None (caps not yet probed) — treated
        # as "assume capable" so first-boot scans don't fail spuriously.
        self._caps_provider = caps_provider or (lambda: None)
        # Best-effort anonymous error-class recorder for telemetry. No-op by
        # default; app.py wires it to ErrorStore.record. Must never raise
        # into the scan path (ErrorStore.record already swallows).
        self._error_recorder = error_recorder or (lambda cls: None)

    @property
    def _subgen(self):
        """Current subgen client (resolved live for onboarding reload)."""
        return self._subgen_provider()

    def _check_can_scan(self) -> None:
        """Raise CompatModeError when /batch isn't available.

        Called at scan submission. Fails fast with a structured reason
        instead of dispatching an async task that errors mid-flight."""
        caps: SubgenCapabilities | None = self._caps_provider()
        if caps is None:
            return  # caps not probed yet — let it proceed
        if not caps.reachable:
            raise CompatModeError(
                "subgen is not reachable. Check the subgen container is running + SUBGEN_URL points at it."
            )
        if not caps.has_batch:
            raise CompatModeError(
                "Scan submission requires the /batch endpoint, which "
                "vanilla mccloud/subgen doesn't ship. Switch to "
                "ghcr.io/coaxk/subarr-subgen for full functionality. "
                "See docs at https://github.com/coaxk/subarr#compat-mode"
            )

    async def aclose(self) -> None:
        async with self._lock:
            tasks = list(self._tasks.values())
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    def start(
        self,
        scan: Scan,
        audio_language_override: str | None = None,
        ignore_forced: bool = False,
        bypass_skip: bool = False,
    ) -> None:
        if audio_language_override:
            self._overrides[scan.id] = audio_language_override
        if ignore_forced:
            self._ignore_forced.add(scan.id)
        if bypass_skip:
            self._bypass_skip.add(scan.id)
        task = asyncio.create_task(self._run(scan.id), name=f"scan-{scan.id}")
        self._tasks[scan.id] = task
        task.add_done_callback(
            lambda t, sid=scan.id: (
                self._tasks.pop(sid, None),
                self._overrides.pop(sid, None),
                self._ignore_forced.discard(sid),
                self._bypass_skip.discard(sid),
            )
        )

    async def subscribe(self, scan_id: str) -> AsyncIterator[dict]:
        """Yields events for a scan until the scan reaches a terminal state.
        Replays the current state as the first event so late subscribers see
        progress already made."""
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        async with self._lock:
            self._subscribers.setdefault(scan_id, set()).add(q)

        try:
            scan = self._store.get(scan_id)
            if scan is not None:
                yield {"event": "snapshot", "data": scan.to_dict()}
                if scan.status in {SCAN_STATUS_DONE, SCAN_STATUS_ERROR}:
                    return

            while True:
                evt = await q.get()
                yield evt
                if evt.get("event") in {"done", "error"}:
                    return
        finally:
            async with self._lock:
                subs = self._subscribers.get(scan_id)
                if subs is not None:
                    subs.discard(q)
                    if not subs:
                        self._subscribers.pop(scan_id, None)

    async def _emit(self, scan_id: str, evt: dict) -> None:
        async with self._lock:
            subs = list(self._subscribers.get(scan_id, ()))
        for q in subs:
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                log.warning("subscriber queue full for scan %s, dropping event", scan_id)

    async def _run(self, scan_id: str) -> None:
        scan = self._store.get(scan_id)
        if scan is None:
            log.error("scan %s vanished before runner could start it", scan_id)
            return

        scan.status = SCAN_STATUS_RUNNING
        self._store.save(scan)
        await self._emit(scan_id, {"event": "start", "data": {"id": scan.id, "paths": scan.paths}})

        for idx, path in enumerate(scan.paths):
            scan.current_index = idx
            result = scan.results[idx]
            result.status = PATH_STATUS_RUNNING
            result.started_at = time.time()
            self._store.save(scan)
            await self._emit(scan_id, {"event": "path_start", "data": {"index": idx, "path": path}})

            try:
                directory = canonical_to_subgen_batch(path)
                override = self._overrides.get(scan_id)
                status_code, body = await self._subgen.batch(
                    directory,
                    reverse=scan.reverse,
                    audio_language_override=override,
                    ignore_forced=scan_id in self._ignore_forced,
                    bypass_skip=scan_id in self._bypass_skip,
                )
                result.subgen_status_code = status_code
                result.subgen_body = body
                result.status, _batch_err = classify_batch_outcome(status_code, body)
                if _batch_err:
                    result.error = _batch_err
            except SubgenUnavailable as e:
                result.status = PATH_STATUS_ERROR
                result.error = str(e)
                self._error_recorder(type(e).__name__)
            except asyncio.CancelledError:
                result.status = PATH_STATUS_ERROR
                result.error = "cancelled"
                result.finished_at = time.time()
                scan.status = SCAN_STATUS_ERROR
                self._store.save(scan)
                await self._emit(scan_id, {"event": "error", "data": {"index": idx, "error": "cancelled"}})
                raise
            except Exception as e:
                result.status = PATH_STATUS_ERROR
                result.error = repr(e)
                self._error_recorder(type(e).__name__)

            result.finished_at = time.time()
            self._store.save(scan)
            await self._emit(
                scan_id,
                {"event": "path_done", "data": {"index": idx, "result": result.to_dict()}},
            )

        scan.current_index = len(scan.paths)
        scan.status = SCAN_STATUS_DONE
        self._store.save(scan)
        await self._emit(scan_id, {"event": "done", "data": scan.to_dict()})
