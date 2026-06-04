"""#131 — ArenaService: background run lifecycle + SSE event stream.

Mirrors ScanRunner's shape (create → start → _run task → subscribe()). Driven
here by a fake CandidateRunner so the lifecycle is tested without subgen.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import subarr
from subarr.arena import ArenaUnsupported, ConfigVariant
from subarr.arena_service import ArenaRun, ArenaService
from subarr.arena_store import ArenaStore

_ARENA_SQL = (Path(subarr.__file__).parent / "migrations" / "009_arena_runs.sql").read_text()


@pytest.fixture
def store(tmp_path):
    s = ArenaStore(tmp_path / "arena.db")
    s._conn.executescript(_ARENA_SQL)  # apply just this migration's schema
    return s


def _srt(line: str) -> str:
    return f"1\n00:00:00,000 --> 00:00:03,000\n{line}\n"


class FakeRunner:
    def __init__(self, outputs, supported=True):
        self._outputs = list(outputs)
        self._supported = supported

    async def preflight(self):
        if not self._supported:
            raise ArenaUnsupported("needs v4.10")

    async def run(self, media_path, *, task, kwargs):
        return self._outputs.pop(0) if self._outputs else None


def _service(store, outputs, supported=True) -> ArenaService:
    return ArenaService(store, build_runner=lambda run: FakeRunner(outputs, supported))


def test_create_persists_pending_run(store):
    svc = _service(store, [])
    run = svc.create("/media/clip.mkv",
                     [ConfigVariant("a", {"beam_size": 5})], source_language="ko")
    assert run.status == "pending"
    persisted = svc.get(run.id)              # read back from SQLite
    assert persisted is not None and persisted.id == run.id
    assert persisted.media_path == "/media/clip.mkv"
    assert persisted.variants == [{"label": "a", "kwargs": {"beam_size": 5}}]
    assert persisted.source_language == "ko"


@pytest.mark.asyncio
async def test_run_completes_and_persists_result(store):
    svc = _service(store, [_srt("source line"), _srt("cand a"), _srt("cand b")])
    run = svc.create("/m.mkv", [ConfigVariant("a", {}), ConfigVariant("b", {})])
    svc.start(run)
    await svc._tasks[run.id]

    persisted = svc.get(run.id)              # the persisted truth, not the in-mem object
    assert persisted.status == "done"
    assert persisted.source_text == "source line"
    assert {o["label"] for o in persisted.outcomes} == {"a", "b"}
    assert all(o["ok"] for o in persisted.outcomes)
    assert persisted.result is not None
    assert "scorecards" in persisted.result and "winner_label" in persisted.result


@pytest.mark.asyncio
async def test_survives_a_fresh_service_on_the_same_store(store):
    """Simulates a restart: a new ArenaService over the same store still sees
    the finished sweep — this is what makes history survive a restart + feed
    the federated tournament."""
    svc = _service(store, [_srt("src"), _srt("a")])
    run = svc.create("/m.mkv", [ConfigVariant("a", {})])
    svc.start(run)
    await svc._tasks[run.id]

    fresh = ArenaService(store, build_runner=lambda r: None)
    again = fresh.get(run.id)
    assert again is not None and again.status == "done"
    assert [r.id for r in fresh.list()] == [run.id]


@pytest.mark.asyncio
async def test_preflight_failure_persists_error(store):
    svc = _service(store, [], supported=False)
    run = svc.create("/m.mkv", [ConfigVariant("a", {})])
    svc.start(run)
    await svc._tasks[run.id]

    persisted = svc.get(run.id)
    assert persisted.status == "error"
    assert "v4.10" in persisted.error
    assert persisted.result is None


@pytest.mark.asyncio
async def test_events_streamed_to_subscriber(store):
    svc = _service(store, [_srt("src"), _srt("a"), None])
    run = svc.create("/m.mkv", [ConfigVariant("a", {}), ConfigVariant("b", {})])

    events: list[dict] = []

    async def collect():
        async for evt in svc.subscribe(run.id):
            events.append(evt)

    consumer = asyncio.create_task(collect())
    await asyncio.sleep(0)      # let the subscriber register before the run emits
    svc.start(run)
    await svc._tasks[run.id]
    await asyncio.wait_for(consumer, timeout=2)

    kinds = [e["event"] for e in events]
    assert kinds[0] == "start"
    assert "source" in kinds
    assert kinds.count("variant") == 2
    assert kinds[-1] == "done"


def test_list_is_newest_first_with_summaries(store):
    svc = _service(store, [])
    a = svc.create("/a.mkv", [ConfigVariant("x", {})])
    a.created_at = 100.0; store.save(a)
    b = svc.create("/b.mkv", [ConfigVariant("y", {}), ConfigVariant("z", {})])
    b.created_at = 200.0; store.save(b)
    listed = svc.list()
    assert [r.id for r in listed] == [b.id, a.id]  # newest first
    s = b.summary()
    assert s["recipe_count"] == 2 and s["status"] == "pending" and "winner" in s
    assert "result" not in s  # summaries stay light (no scorecards)


def test_reconcile_marks_interrupted_runs_errored(store):
    svc = _service(store, [])
    run = svc.create("/m.mkv", [ConfigVariant("a", {})])
    run.status = "running"; store.save(run)   # pretend it was mid-flight at crash
    n = store.reconcile_interrupted()
    assert n == 1
    after = svc.get(run.id)
    assert after.status == "error" and "interrupted" in after.error
