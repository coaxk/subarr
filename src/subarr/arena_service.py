"""#131 — ArenaService: background lifecycle + SSE event stream for a sweep.

Mirrors ScanRunner: `create()` makes a pending run, `start()` schedules an
asyncio task that drives `run_arena`, and `subscribe()` is an async generator
the SSE endpoint streams.

Sweeps PERSIST (ArenaStore / SQLite): history survives a restart, and the
results are the substrate the federated tournament (#124) is built on. The
service write-throughs to the store on every state transition, so `get()` /
`list()` always read persisted truth (and survive navigation + restart). The
SSE subscribers + asyncio tasks stay in-memory (transient).

The run state stays light on purpose: live `outcomes` carry label/ok/error
(not the full SRT), and `result` is the serialized `TournamentResult`.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import asdict
from typing import AsyncIterator, Callable

from .arena import CandidateRunner, ConfigVariant, run_arena
from .arena_store import ArenaRun, ArenaStore  # re-exported for stable imports

__all__ = ["ArenaRun", "ArenaStore", "ArenaService"]


class ArenaService:
    def __init__(self, store: ArenaStore, build_runner: Callable[[ArenaRun], CandidateRunner],
                 explainer=None, max_concurrent: int = 1):
        self._store = store
        self._build_runner = build_runner
        self._explainer = explainer  # async (result_dict, media_path) -> str|None
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        # Single-flight by default: each sweep runs CPU-heavy QE/torch, so N
        # concurrent sweeps saturate the box (observed: 6 stacked sweeps pinned
        # subarr-next + cascade-failed). Submits queue; only `max_concurrent`
        # process at once.
        self._sema = asyncio.Semaphore(max_concurrent)

    # ── store (persisted) ────────────────────────────────────────────────────
    def create(self, media_path: str, variants: list[ConfigVariant],
               source_language: str | None = None) -> ArenaRun:
        run = ArenaRun(
            id=uuid.uuid4().hex[:12],
            media_path=media_path,
            variants=[{"label": v.label, "kwargs": v.kwargs} for v in variants],
            source_language=source_language,
            created_at=time.time(),
        )
        self._store.save(run)
        return run

    def get(self, run_id: str) -> ArenaRun | None:
        return self._store.get(run_id)

    def list(self) -> list[ArenaRun]:
        return self._store.list()

    def delete(self, run_id: str) -> bool:
        return self._store.delete(run_id)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self, run: ArenaRun) -> None:
        self._tasks[run.id] = asyncio.create_task(self._run(run), name=f"arena-{run.id}")

    async def aclose(self) -> None:
        for t in list(self._tasks.values()):
            t.cancel()

    # ── SSE ────────────────────────────────────────────────────────────────
    def _emit(self, run_id: str, evt: dict) -> None:
        for q in list(self._subscribers.get(run_id, ())):
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                pass  # a slow consumer drops events; it can re-GET the state

    async def subscribe(self, run_id: str) -> AsyncIterator[dict]:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.setdefault(run_id, set()).add(q)
        try:
            while True:
                evt = await q.get()
                yield evt
                if evt.get("event") in ("done", "error"):
                    return
        finally:
            subs = self._subscribers.get(run_id)
            if subs:
                subs.discard(q)
                if not subs:
                    self._subscribers.pop(run_id, None)

    async def _run(self, run: ArenaRun) -> None:
        run_id = run.id
        # Queued until a slot frees — keeps concurrent submits from running
        # their heavy QE work in parallel and saturating the box.
        run.status = "queued"
        run.outcomes = {"clips": [], "done": 0, "total": 0}  # progress scaffold
        self._store.save(run)
        self._emit(run_id, {"event": "queued", "data": {"id": run.id}})
        try:
            async with self._sema:
                run.status = "running"
                self._store.save(run)
                self._emit(run_id, {"event": "start",
                                    "data": {"id": run.id, "variants": [v["label"] for v in run.variants]}})
                await self._execute(run)
        except asyncio.CancelledError:
            run.status = "error"
            run.error = "cancelled"
            self._store.save(run)
            raise
        except Exception as e:
            run.status = "error"
            run.error = str(e)
            self._store.save(run)
            self._emit(run_id, {"event": "error", "data": {"error": str(e)}})

    async def _execute(self, run: ArenaRun) -> None:
        run_id = run.id
        runner = self._build_runner(run)
        variants = [ConfigVariant(v["label"], v["kwargs"]) for v in run.variants]
        per_clip = run.outcomes  # alias the progress dict

        def on_clip(idx: int, kind: str, total: int) -> None:
            if not per_clip["clips"]:
                per_clip["clips"] = [{"kind": None, "status": "pending"} for _ in range(total)]
                per_clip["total"] = total * (len(variants) + 1)  # +1 source per clip
            for j in range(idx):
                per_clip["clips"][j]["status"] = "done"
            per_clip["clips"][idx] = {"kind": kind, "status": "running"}
            self._store.save(run)
            self._emit(run_id, {"event": "clip", "data": {"idx": idx, "kind": kind, "total": total}})

        def on_step() -> None:
            per_clip["done"] += 1
            self._store.save(run)
            self._emit(run_id, {"event": "step", "data": {"done": per_clip["done"]}})

        result = await run_arena(run.media_path, variants, runner=runner,
                                 on_clip=on_clip, on_step=on_step)
        for c in per_clip["clips"]:
            c["status"] = "done"
        serialized = asdict(result)
        if self._explainer is not None:
            try:
                serialized["explanation"] = await self._explainer(serialized, run.media_path)
            except Exception:  # explanation is a nicety — never fail the sweep
                serialized["explanation"] = None
        run.result = serialized
        # [#23] persist the detected source language for the table + per-language
        # grouping (don't clobber an explicitly-set one with a null detection).
        run.source_language = serialized.get("source_language") or run.source_language
        run.status = "done"
        self._store.save(run)
        self._emit(run_id, {"event": "done", "data": run.to_dict()})
