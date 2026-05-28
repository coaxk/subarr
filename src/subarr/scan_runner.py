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


class CompatModeError(RuntimeError):
    """The detected subgen build doesn't support a capability subarr's
    scan flow needs. Surfaces a clear UI message instead of a vague
    HTTP error or silent no-op."""


class ScanRunner:
    def __init__(self, subgen: SubgenClient, store: ScanStore,
                 caps_provider=None):
        self._subgen = subgen
        self._store = store
        self._tasks: dict[str, asyncio.Task] = {}
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()
        # Callable returning the cached SubgenCapabilities snapshot.
        # We don't probe per-scan — caps are stable per app boot.
        # The provider may return None (caps not yet probed) — treated
        # as "assume capable" so first-boot scans don't fail spuriously.
        self._caps_provider = caps_provider or (lambda: None)

    def _check_can_scan(self) -> None:
        """Raise CompatModeError when /batch isn't available.

        Called at scan submission. Fails fast with a structured reason
        instead of dispatching an async task that errors mid-flight."""
        caps: SubgenCapabilities | None = self._caps_provider()
        if caps is None:
            return  # caps not probed yet — let it proceed
        if not caps.reachable:
            raise CompatModeError(
                "subgen is not reachable. Check the subgen container is "
                "running + SUBGEN_URL points at it."
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

    def start(self, scan: Scan) -> None:
        task = asyncio.create_task(self._run(scan.id), name=f"scan-{scan.id}")
        self._tasks[scan.id] = task
        task.add_done_callback(lambda t, sid=scan.id: self._tasks.pop(sid, None))

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
                status_code, body = await self._subgen.batch(directory, reverse=scan.reverse)
                result.subgen_status_code = status_code
                result.subgen_body = body
                walked = body.get("walked", 0) if isinstance(body, dict) else 0
                queued = body.get("queued", 0) if isinstance(body, dict) else 0
                skipped = body.get("skipped", 0) if isinstance(body, dict) else 0
                if status_code == 200 and walked > 0 and queued > 0:
                    result.status = PATH_STATUS_OK
                elif status_code == 200 and walked > 0 and queued == 0 and skipped > 0:
                    # Subgen walked the path but skipped everything — usually
                    # because SKIP_IF_TARGET_SUBTITLES_EXIST hit (embedded EN
                    # already present) or SKIP_IF_AUDIO_LANGUAGES matched.
                    # NOT an error; the user just doesn't need subs for this.
                    result.status = PATH_STATUS_SKIPPED
                    reasons = []
                    pending_detect = body.get("pending_language_detect", 0)
                    already_q = body.get("already_in_queue", 0)
                    no_audio = body.get("no_audio", 0)
                    if skipped:
                        reasons.append(f"subgen skipped {skipped} (target sub exists / audio lang match)")
                    if already_q:
                        reasons.append(f"{already_q} already in queue")
                    if no_audio:
                        reasons.append(f"{no_audio} no audio")
                    if pending_detect:
                        reasons.append(f"{pending_detect} pending lang detect")
                    result.error = "; ".join(reasons) if reasons else "skipped"
                elif status_code == 200 and walked > 0 and queued == 0 \
                        and (body.get("already_in_queue", 0) > 0):
                    # Already in subgen's queue — treat as OK; it'll process.
                    result.status = PATH_STATUS_OK
                elif status_code == 404 or walked == 0:
                    result.status = PATH_STATUS_EMPTY
                else:
                    result.status = PATH_STATUS_ERROR
                    result.error = f"unexpected subgen response: {status_code}"
            except SubgenUnavailable as e:
                result.status = PATH_STATUS_ERROR
                result.error = str(e)
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
