"""Background async folder walker for ffprobe.

Walks <media_root>/<canonical> recursively, runs ffprobe on every video
file whose (mtime, size) doesn't match the cached entry, upserts the
result. Emits progress events through an asyncio.Queue so /api/probe/walk/
{id}/events can stream SSE to the frontend.

One walker instance per app. Multiple concurrent walks supported via
walk_id keys.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from .config import settings
from .media_probe import ProbeError, parse_ffprobe_json, probe
from .paths import VIDEO_EXTS, fs_to_canonical
from .probe_store import ProbeStore

log = logging.getLogger(__name__)

# Concurrency: ffprobe is cheap per file but we don't want to fork dozens
# at once if a user walks a 1000-file folder. Cap at 4.
WALK_CONCURRENCY = 4


class WalkState:
    def __init__(self, walk_id: str, root_canonical: str):
        self.id = walk_id
        self.root = root_canonical
        self.status = "running"  # running | done | error | cancelled
        self.total_files = 0
        self.processed = 0
        self.cached_hits = 0
        self.probed = 0
        self.errors: list[dict] = []
        self.started_at = time.time()
        self.finished_at: float | None = None
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=512)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "root": self.root,
            "status": self.status,
            "total_files": self.total_files,
            "processed": self.processed,
            "cached_hits": self.cached_hits,
            "probed": self.probed,
            "errors": self.errors[:10],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class ProbeWalker:
    def __init__(self, probe_store: ProbeStore):
        self._store = probe_store
        self._walks: dict[str, WalkState] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    def get(self, walk_id: str) -> WalkState | None:
        return self._walks.get(walk_id)

    def list_all(self) -> list[WalkState]:
        return list(self._walks.values())

    async def start_walk(self, root_canonical: str) -> WalkState:
        # Dedupe: if a walk for this exact root is already running, hand
        # back its WalkState instead of starting a parallel one. Without
        # this, /run-now while the scheduler is mid-walk (or two scheduler
        # ticks close together) burns GPU/IO re-probing the same files.
        # Caller gets the existing walk_id so subscribers don't fragment.
        for existing in self._walks.values():
            if existing.root == root_canonical and existing.status == "running":
                log.info(
                    "start_walk: walk %s for root=%r already running (%d/%d processed) — reusing",
                    existing.id, root_canonical, existing.processed, existing.total_files,
                )
                return existing

        walk_id = uuid.uuid4().hex[:16]
        state = WalkState(walk_id, root_canonical)
        self._walks[walk_id] = state
        task = asyncio.create_task(self._run(state), name=f"probe-walk-{walk_id}")
        self._tasks[walk_id] = task
        task.add_done_callback(lambda t, wid=walk_id: self._tasks.pop(wid, None))
        return state

    async def aclose(self) -> None:
        for t in list(self._tasks.values()):
            t.cancel()
        for t in list(self._tasks.values()):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    async def subscribe(self, walk_id: str) -> AsyncIterator[dict]:
        state = self._walks.get(walk_id)
        if state is None:
            return
        yield {"event": "snapshot", "data": state.to_dict()}
        if state.status in {"done", "error", "cancelled"}:
            return
        # Live subscribers tap a fresh queue endpoint clone via state's queue;
        # for simplicity we just poll the state every 0.5s and emit deltas.
        last_processed = state.processed
        last_status = state.status
        while state.status == "running":
            await asyncio.sleep(0.5)
            if state.processed != last_processed:
                yield {"event": "progress", "data": state.to_dict()}
                last_processed = state.processed
            if state.status != last_status:
                last_status = state.status
                break
        yield {"event": state.status, "data": state.to_dict()}

    async def _run(self, state: WalkState) -> None:
        try:
            root_fs = settings.media_root / state.root
            if not root_fs.exists() or not root_fs.is_dir():
                state.status = "error"
                state.errors.append({"error": f"root not found: {state.root}"})
                state.finished_at = time.time()
                return

            # #228: full-library rglob blocks the event loop for the entire
            # enumeration (seconds → minutes on a real library). Offload so
            # /api/health and dashboard polls keep responding while we walk.
            def _enumerate_videos():
                return [
                    p for p in root_fs.rglob("*")
                    if p.is_file() and p.suffix.lower() in VIDEO_EXTS
                ]
            video_files = await asyncio.to_thread(_enumerate_videos)
            state.total_files = len(video_files)
            log.info("probe walk %s: %d files under %s", state.id, state.total_files, state.root)

            sem = asyncio.Semaphore(WALK_CONCURRENCY)

            async def _one(p: Path):
                async with sem:
                    try:
                        st = p.stat()
                    except OSError as e:
                        state.errors.append({"path": str(p), "error": f"stat: {e}"})
                        state.processed += 1
                        return
                    canonical = fs_to_canonical(p)
                    cached = self._store.get(canonical, mtime=st.st_mtime, size=st.st_size)
                    if cached is not None:
                        state.cached_hits += 1
                        state.processed += 1
                        return
                    try:
                        result = await probe(p)
                        result.canonical_path = canonical
                        self._store.upsert(
                            canonical_path=canonical,
                            mtime=st.st_mtime, size=st.st_size,
                            result=result,
                        )
                        state.probed += 1
                    except ProbeError as e:
                        state.errors.append({"path": canonical, "error": str(e)})
                    except Exception as e:
                        state.errors.append({"path": canonical, "error": repr(e)})
                    finally:
                        state.processed += 1

            await asyncio.gather(*(_one(p) for p in video_files))
            state.status = "done"
            state.finished_at = time.time()
            log.info(
                "probe walk %s done: %d files, %d cached, %d probed, %d errors",
                state.id, state.total_files, state.cached_hits, state.probed, len(state.errors),
            )
        except asyncio.CancelledError:
            state.status = "cancelled"
            state.finished_at = time.time()
            raise
        except Exception as e:
            log.exception("probe walk %s failed: %s", state.id, e)
            state.status = "error"
            state.errors.append({"error": repr(e)})
            state.finished_at = time.time()
