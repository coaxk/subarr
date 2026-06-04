"""#131 — ArenaService: background lifecycle + SSE event stream for a sweep.

Mirrors ScanRunner: `create()` makes a pending run, `start()` schedules an
asyncio task that drives `run_arena`, and `subscribe()` is an async generator
the SSE endpoint streams. Runs are in-memory (an arena run is a transient
experiment — it doesn't need to survive a restart like an audited scan does).

The run state stays light on purpose: live `outcomes` carry label/ok/error
(not the full SRT), and `result` is the serialized `TournamentResult`. The
raw candidate subtitles are not stored — the ranking + signals are what the
tuning lab needs; surfacing the winning SRT text is a later UI nicety.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, AsyncIterator, Callable

from .arena import CandidateRunner, ConfigVariant, run_arena


@dataclass
class ArenaRun:
    id: str
    media_path: str
    variants: list[dict[str, Any]]          # [{label, kwargs}]
    source_language: str | None = None
    status: str = "pending"                  # pending | running | done | error
    source_text: str | None = None
    outcomes: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None     # serialized TournamentResult
    error: str | None = None
    created_at: float = 0.0                   # epoch seconds; for newest-first ordering

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> dict[str, Any]:
        """Lightweight shape for the sweeps list — no heavy scorecards. The
        full detail (with the ranked table) is fetched per-run via /{id}."""
        return {
            "id": self.id,
            "media_path": self.media_path,
            "status": self.status,
            "recipe_count": len(self.variants),
            "done_count": len(self.outcomes),
            "winner": (self.result or {}).get("winner_label"),
            "error": self.error,
            "created_at": self.created_at,
        }


class ArenaService:
    def __init__(self, build_runner: Callable[[ArenaRun], CandidateRunner]):
        self._build_runner = build_runner
        self._runs: dict[str, ArenaRun] = {}
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    # ── store ──────────────────────────────────────────────────────────────
    def create(self, media_path: str, variants: list[ConfigVariant],
               source_language: str | None = None) -> ArenaRun:
        run = ArenaRun(
            id=uuid.uuid4().hex[:12],
            media_path=media_path,
            variants=[{"label": v.label, "kwargs": v.kwargs} for v in variants],
            source_language=source_language,
            created_at=time.time(),
        )
        self._runs[run.id] = run
        return run

    def get(self, run_id: str) -> ArenaRun | None:
        return self._runs.get(run_id)

    def list(self) -> list[ArenaRun]:
        return sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self, run: ArenaRun) -> None:
        self._tasks[run.id] = asyncio.create_task(self._run(run.id), name=f"arena-{run.id}")

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

    async def _run(self, run_id: str) -> None:
        run = self._runs[run_id]
        run.status = "running"
        self._emit(run_id, {"event": "start",
                            "data": {"id": run.id, "variants": [v["label"] for v in run.variants]}})
        try:
            runner = self._build_runner(run)
            variants = [ConfigVariant(v["label"], v["kwargs"]) for v in run.variants]

            def on_source(text: str | None) -> None:
                run.source_text = text
                self._emit(run_id, {"event": "source", "data": {"has_text": bool(text)}})

            def on_variant(outcome) -> None:
                d = {"label": outcome.label, "ok": outcome.srt_text is not None,
                     "error": outcome.error}
                run.outcomes.append(d)
                self._emit(run_id, {"event": "variant", "data": d})

            result = await run_arena(run.media_path, variants, runner=runner,
                                     on_source=on_source, on_variant=on_variant)
            run.result = asdict(result.tournament)
            run.status = "done"
            self._emit(run_id, {"event": "done", "data": run.to_dict()})
        except asyncio.CancelledError:
            run.status = "error"
            run.error = "cancelled"
            raise
        except Exception as e:
            run.status = "error"
            run.error = str(e)
            self._emit(run_id, {"event": "error", "data": {"error": str(e)}})
