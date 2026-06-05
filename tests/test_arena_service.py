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

    async def prepare(self, media_path):
        return [{"kind": "speech", "ranges": [(0.0, 3.0)]}]   # one clip

    async def run(self, clip_idx, *, task, kwargs):
        return self._outputs.pop(0) if self._outputs else None

    async def cleanup(self):
        pass


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


def test_save_coerces_numpy_like_scalars_in_result(store):
    # A numpy float32 leaking from the QE judge is not JSON-serializable and
    # crashed store.save → the run stayed "running" forever. The store must
    # coerce any scalar that quacks like numpy (has .item()) rather than crash.
    class FakeNpScalar:
        def __init__(self, v): self._v = v
        def item(self): return self._v

    svc = _service(store, [])
    run = svc.create("/m.mkv", [ConfigVariant("a", {})])
    run.status = "done"
    run.result = {"winner": "a", "score": FakeNpScalar(0.581)}
    store.save(run)                          # must not raise
    persisted = svc.get(run.id)
    assert persisted.status == "done"
    assert persisted.result["score"] == 0.581


@pytest.mark.asyncio
async def test_concurrent_sweeps_serialize_one_at_a_time(store):
    # A user firing several sweeps must NOT run them all at once — each runs
    # CPU-heavy QE and N concurrent sweeps saturate the box (observed: 6
    # concurrent Nutuk sweeps pinned subarr-next + cascade-failed). Submits
    # should queue; only one processes at a time.
    gate = asyncio.Event()
    processing_started = asyncio.Event()
    started = []

    class BlockingRunner:
        def __init__(self, run): self.arun = run   # NOT self.run — would shadow run()
        async def preflight(self): pass
        async def prepare(self, media_path):
            started.append(self.arun.id)
            processing_started.set()
            await gate.wait()                      # hold the single slot
            return [{"kind": "speech", "ranges": []}]
        async def run(self, clip_idx, *, task, kwargs): return _srt("x")
        async def cleanup(self): pass

    svc = ArenaService(store, build_runner=lambda run: BlockingRunner(run))
    r1 = svc.create("/a.mkv", [ConfigVariant("default", {})])
    r2 = svc.create("/b.mkv", [ConfigVariant("default", {})])
    svc.start(r1); svc.start(r2)

    await asyncio.wait_for(processing_started.wait(), 2)   # r1 is in the slot
    for _ in range(50):                                    # let r2's task reach the gate
        if svc.get(r2.id).status == "queued": break
        await asyncio.sleep(0.01)

    assert started == [r1.id]                              # r2 has NOT started processing
    assert svc.get(r1.id).status == "running"
    assert svc.get(r2.id).status == "queued"

    gate.set()                                             # release; r1 finishes, r2 proceeds
    await asyncio.wait_for(asyncio.gather(svc._tasks[r1.id], svc._tasks[r2.id]), 5)
    assert started == [r1.id, r2.id]
    g1, g2 = svc.get(r1.id), svc.get(r2.id)
    assert g1.status == "done", f"r1 {g1.status} err={g1.error}"
    assert g2.status == "done", f"r2 {g2.status} err={g2.error}"


def test_source_language_persists_on_update(store):
    # #23: the detected language is set AFTER create (the initial INSERT has it
    # None), on a later save() — so the UPSERT's UPDATE clause must include it,
    # else it's silently dropped (result carried 'korean' but run stayed None).
    svc = _service(store, [])
    run = svc.create("/m.mkv", [ConfigVariant("a", {})])
    assert svc.get(run.id).source_language is None
    run.source_language = "korean"
    store.save(run)                                   # UPDATE (ON CONFLICT) path
    assert svc.get(run.id).source_language == "korean"


@pytest.mark.asyncio
async def test_run_completes_and_persists_result(store):
    svc = _service(store, [_srt("source line"), _srt("cand a"), _srt("cand b")])
    run = svc.create("/m.mkv", [ConfigVariant("a", {}), ConfigVariant("b", {})])
    svc.start(run)
    await svc._tasks[run.id]

    persisted = svc.get(run.id)              # the persisted truth, not the in-mem object
    assert persisted.status == "done"
    # outcomes is now the progress scaffold (1 clip × (source + 2 recipes) = 3 steps)
    assert persisted.outcomes["done"] == 3 and persisted.outcomes["total"] == 3
    assert persisted.result is not None
    assert "aggregate" in persisted.result and "winner" in persisted.result and "per_clip" in persisted.result


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
    assert kinds[0] == "queued"            # serialize: every run is queued before it starts
    assert kinds[1] == "start"
    assert "clip" in kinds
    assert kinds.count("step") == 3       # 1 clip × (source + 2 recipes)
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


def test_delete_removes_run(store):
    svc = _service(store, [])
    run = svc.create("/m.mkv", [ConfigVariant("a", {})])
    assert svc.get(run.id) is not None
    assert svc.delete(run.id) is True
    assert svc.get(run.id) is None
    assert svc.delete("nope") is False
