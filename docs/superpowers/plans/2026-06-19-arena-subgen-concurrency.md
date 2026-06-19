# Arena ↔ subgen concurrency coordination — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make subarr's Tuning-Lab arena respect — and visibly reflect — the user's real subgen `CONCURRENT_TRANSCRIPTIONS`, so its `/asr` path stops bypassing the concurrency limit the normal queue already honours.

**Architecture:** A single invariant — `subgen.processing_count + arena_in_flight < N` (N read live from subgen) — gates both the pending-queue feeder and the arena. A pure helper defines the invariant once; the feeder and arena each consult it. Two UI surfaces make arena GPU use visible: a `waiting_for_capacity` state on the Tuning Lab run card, and a read-only "sweep using a slot" indicator on the Queue page. When N is unknown (old/unreachable subgen) the gate is a no-op, so the feature is dormant-safe.

**Tech Stack:** Python 3.12 + FastAPI + httpx (async); SQLite stores; React-from-CDN + esbuild IIFE bundles; pytest (`PYTHONPATH=src`); vitest for frontend.

**Spec:** `docs/superpowers/specs/2026-06-19-arena-subgen-concurrency-design.md`

**Conventions for every task:**
- Run backend tests with `PYTHONPATH=src python -m pytest <path> -q` from `C:\Projects\subarr`.
- The PostToolUse ruff hook strips a just-added unused import — add an import and its first use in the same edit.
- Commit after each task. Solo repo: final merge is `gh pr merge NN --squash --admin --delete-branch`.
- Branch already created: `feat/arena-subgen-concurrency`.

---

## File structure

| File | Responsibility | Change |
|---|---|---|
| `src/subarr/subgen_capacity.py` | The capacity invariant, as one pure function. | **Create** |
| `tests/test_subgen_capacity.py` | Unit tests for the invariant. | **Create** |
| `src/subarr/subgen_client.py` | `SubgenCapabilities.concurrent_transcriptions` + parse it from `/queue`. | Modify |
| `src/subarr/arena_service.py` | Capacity gate + `waiting_for_capacity` status + `inflight_count()`. | Modify |
| `src/subarr/pending_feeder.py` | Capacity gate in `tick()`. | Modify |
| `src/subarr/app.py` | Wire providers into the feeder + arena. | Modify |
| `src/subarr/routers/queue.py` | Surface B: `arena_active` field on `GET /api/queue`. | Modify |
| `src/subarr/static/v1/home-hifi/arena.jsx` | Surface A: `waiting_for_capacity` pill + run-card copy. | Modify |
| `src/subarr/static/v1/home-hifi/queue.jsx` | Surface B: read-only "sweep using a slot" indicator. | Modify |
| `subarr-subgen: subgen.py` | Piece 1 (r10): expose `concurrent_transcriptions` in `/queue` capabilities. | Modify (separate repo) |

---

## Task 1: Capacity invariant helper

**Files:**
- Create: `src/subarr/subgen_capacity.py`
- Test: `tests/test_subgen_capacity.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_subgen_capacity.py
from subarr.subgen_capacity import subgen_capacity_free


def test_capacity_free_when_n_none_is_always_true():
    # Old / unreachable subgen: gate disabled, never blocks (no regression).
    assert subgen_capacity_free(processing_count=99, arena_in_flight=99, n=None) is True


def test_single_job_serializes():
    # N=1: any one transcription in flight blocks the rest.
    assert subgen_capacity_free(processing_count=0, arena_in_flight=0, n=1) is True
    assert subgen_capacity_free(processing_count=1, arena_in_flight=0, n=1) is False
    assert subgen_capacity_free(processing_count=0, arena_in_flight=1, n=1) is False


def test_default_two_allows_one_batch_plus_one_sweep():
    assert subgen_capacity_free(processing_count=1, arena_in_flight=0, n=2) is True
    assert subgen_capacity_free(processing_count=1, arena_in_flight=1, n=2) is False


def test_large_n_never_throttles():
    assert subgen_capacity_free(processing_count=4, arena_in_flight=1, n=1000) is True
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src python -m pytest tests/test_subgen_capacity.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'subarr.subgen_capacity'`.

- [ ] **Step 3: Implement the helper**

```python
# src/subarr/subgen_capacity.py
"""The single definition of subarr's subgen GPU-concurrency invariant.

subgen runs at most N = CONCURRENT_TRANSCRIPTIONS transcriptions at once via its
worker pool, but the Tuning-Lab arena's /asr "direct task" path runs OUTSIDE that
pool (and is invisible to subgen's GET /queue). To keep total concurrent GPU work
within the user's real limit, BOTH producers subarr drives — the pending-queue
feeder and the arena — consult this before committing work.

N is read live from subgen (capabilities.concurrent_transcriptions); when it is
unknown (old subgen, or subgen unreachable) the gate is disabled so behaviour is
exactly as before — no regression, no false stalls.
"""

from __future__ import annotations


def subgen_capacity_free(*, processing_count: int, arena_in_flight: int, n: int | None) -> bool:
    """True when subarr may start one more GPU-bound transcription.

    ``processing_count`` is subgen's reported processing count (includes foreign
    Plex/Bazarr jobs). ``arena_in_flight`` is subarr's count of arena /asr
    transcriptions currently running. ``n`` is subgen's concurrency limit, or
    None when unknown — in which case the gate is open (disabled)."""
    if n is None:
        return True
    return (processing_count + arena_in_flight) < n
```

- [ ] **Step 4: Run to verify pass**

Run: `PYTHONPATH=src python -m pytest tests/test_subgen_capacity.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/subarr/subgen_capacity.py tests/test_subgen_capacity.py
git commit -m "feat: subgen_capacity_free invariant helper (arena/feeder shared gate)"
```

---

## Task 2: Expose `N` on SubgenCapabilities

**Files:**
- Modify: `src/subarr/subgen_client.py` (dataclass field ~128, parse ~327, constructor ~356, `to_dict` ~149)
- Test: `tests/test_subgen_client.py` (add to the existing capabilities tests; if no probe test exists, add one with a mocked transport mirroring `tests/test_arena.py`'s `_client`)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_subgen_client.py  (add)
import httpx
import pytest
from subarr.subgen_client import SubgenClient


def _client(handler):
    c = SubgenClient(base_url="http://fake:9000")
    c._client = httpx.AsyncClient(base_url="http://fake:9000", transport=httpx.MockTransport(handler))
    return c


@pytest.mark.asyncio
async def test_probe_reads_concurrent_transcriptions():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/queue":
            return httpx.Response(200, json={
                "queued": [], "processing": [],
                "capabilities": {"concurrent_transcriptions": 3},
            })
        return httpx.Response(200, json={"version": "Subgen 2026.05.3-r10, ..."})
    caps = await _client(handler).probe_capabilities()
    assert caps.concurrent_transcriptions == 3


@pytest.mark.asyncio
async def test_probe_concurrent_transcriptions_absent_is_none():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/queue":
            return httpx.Response(200, json={"queued": [], "processing": [], "capabilities": {}})
        return httpx.Response(200, json={"version": "Subgen 2026.05.3-r9, ..."})
    caps = await _client(handler).probe_capabilities()
    assert caps.concurrent_transcriptions is None
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src python -m pytest tests/test_subgen_client.py -q -k concurrent_transcriptions`
Expected: FAIL — `AttributeError: 'SubgenCapabilities' object has no attribute 'concurrent_transcriptions'`.

- [ ] **Step 3: Add the field, parse, constructor, and to_dict entry**

In `src/subarr/subgen_client.py`, add the field right after `release_tag` (~line 128, before `def to_dict`):

```python
    # r10 capability (#202 follow-up): subgen's live CONCURRENT_TRANSCRIPTIONS
    # value — the number of transcriptions its worker pool runs at once. subarr
    # uses it as the GPU-concurrency budget that both the feeder and the arena
    # gate on (see subgen_capacity). None on older subgen that doesn't publish
    # it → the gate stays disabled (dormant-safe).
    concurrent_transcriptions: int | None = None
```

In `to_dict` (~line 149, before the closing `}`), add:

```python
            "concurrent_transcriptions": self.concurrent_transcriptions,
```

In `probe_capabilities`, with the other `caps_block` reads (~line 327, after `runtime_config = ...`):

```python
                            _ct = caps_block.get("concurrent_transcriptions")
                            concurrent_transcriptions = _ct if isinstance(_ct, int) and _ct > 0 else None
```

Initialise the local with the other defaults (~line 296, after `runtime_config = False`):

```python
        concurrent_transcriptions: int | None = None
```

Pass it into the `SubgenCapabilities(...)` constructor (~line 356, after `runtime_config=runtime_config,`):

```python
            concurrent_transcriptions=concurrent_transcriptions,
```

(`unreachable()` needs no change — the field defaults to `None`.)

- [ ] **Step 4: Run to verify pass**

Run: `PYTHONPATH=src python -m pytest tests/test_subgen_client.py -q -k concurrent_transcriptions`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/subarr/subgen_client.py tests/test_subgen_client.py
git commit -m "feat: parse subgen concurrent_transcriptions into SubgenCapabilities"
```

---

## Task 3: Arena capacity gate + waiting state + inflight count

**Files:**
- Modify: `src/subarr/arena_service.py` (`__init__` ~72, `_run` ~166)
- Test: `tests/test_arena_service.py`

The arena needs subgen's processing count and N. Inject a `subgen_provider` and `caps_provider` (same closure pattern the feeder uses). Add an in-memory `_inflight` counter (0/1 — the sweep semaphore caps concurrency at 1) and an `inflight_count()` accessor. Between acquiring the sweep semaphore and transcribing, poll capacity; while blocked, set status `waiting_for_capacity` and emit an SSE event.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_arena_service.py  (add)
import asyncio
import pytest
from subarr.arena_service import ArenaService
from subarr.arena_store import ArenaStore
from subarr.arena import ConfigVariant


class _FakeSubgen:
    def __init__(self, processing):
        self._processing = processing
    async def queue(self):
        return {"queued": [], "processing": self._processing,
                "queued_count": 0, "processing_count": len(self._processing)}


def _svc(tmp_path, *, processing, n, runner=None):
    store = ArenaStore(tmp_path / "arena.db")
    sub = _FakeSubgen(processing)
    return ArenaService(
        store,
        build_runner=runner or (lambda run: _NoopRunner()),
        subgen_provider=lambda: sub,
        caps_provider=lambda: type("C", (), {"concurrent_transcriptions": n})(),
    ), sub


class _NoopRunner:
    async def preflight(self): ...
    async def prepare(self, p): return []
    async def cleanup(self): ...


def test_inflight_count_starts_zero(tmp_path):
    svc, _ = _svc(tmp_path, processing=[], n=1)
    assert svc.inflight_count() == 0


@pytest.mark.asyncio
async def test_sweep_waits_for_capacity_then_runs(tmp_path):
    # N=1 and subgen already processing one job → sweep must enter
    # waiting_for_capacity, then proceed once the job clears.
    svc, sub = _svc(tmp_path, processing=[{"path": "/m/x.mkv"}], n=1)
    run = svc.create("TV/Show/ep.mkv", [ConfigVariant("base", {})])
    events = []
    async def collect():
        async for evt in svc.subscribe(run.id):
            events.append(evt["event"])
            if evt["event"] in ("done", "error"):
                return
    svc.start(run)
    consumer = asyncio.create_task(collect())
    await asyncio.sleep(0.2)
    assert svc.get(run.id).status == "waiting_for_capacity"
    sub._processing = []  # capacity frees
    await asyncio.wait_for(consumer, timeout=5)
    assert "waiting_for_capacity" in events
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src python -m pytest tests/test_arena_service.py -q -k "inflight or waits_for_capacity"`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'subgen_provider'`.

- [ ] **Step 3: Implement**

In `arena_service.py`, add a module constant near the top (after imports):

```python
CAPACITY_POLL_INTERVAL_S = 3.0
# After this many consecutive unreadable-subgen probes, proceed anyway rather
# than stalling a sweep forever on a flaky /queue (fail open — spec: bounded wait).
CAPACITY_PROBE_FAIL_OPEN_AFTER = 3
```

Extend `__init__` (after `track_info=None,`):

```python
        subgen_provider=None,
        caps_provider=None,
```
and in the body (after `self._sema = asyncio.Semaphore(max_concurrent)`):

```python
        # GPU-concurrency gate (see subgen_capacity). Both optional: when either
        # provider is absent or yields None, the gate is disabled (dormant-safe).
        self._subgen_provider = subgen_provider
        self._caps_provider = caps_provider
        self._inflight = 0  # arena /asr transcriptions currently running (0/1)
```

Add the accessor (after `def get`):

```python
    def inflight_count(self) -> int:
        """Arena /asr transcriptions currently running — the feeder counts this
        toward subgen's GPU load (the arena bypasses subgen's worker pool)."""
        return self._inflight
```

Add the gate as a method (near `_run`):

```python
    async def _await_capacity(self, run: "ArenaRun") -> None:
        """Block until subgen has a free GPU slot, surfacing a waiting state.

        Disabled (returns immediately) when no subgen/caps provider is wired or
        N is unknown. Waits indefinitely while subgen is genuinely at capacity
        (that's the point), but fails OPEN after CAPACITY_PROBE_FAIL_OPEN_AFTER
        consecutive unreadable probes so a flaky /queue never hangs a sweep."""
        if self._subgen_provider is None or self._caps_provider is None:
            return
        from .subgen_capacity import subgen_capacity_free
        emitted = False
        fails = 0
        while True:
            caps = self._caps_provider()
            n = getattr(caps, "concurrent_transcriptions", None) if caps else None
            if n is None:
                return  # gate disabled
            subgen = self._subgen_provider()
            try:
                q = await subgen.queue()
                fails = 0
            except Exception:  # noqa: BLE001 — unreadable queue
                fails += 1
                if fails >= CAPACITY_PROBE_FAIL_OPEN_AFTER:
                    return  # fail open: don't hang the sweep
                await asyncio.sleep(CAPACITY_POLL_INTERVAL_S)
                continue
            processing = q.get("processing_count")
            if not isinstance(processing, int):
                processing = len(q.get("processing") or [])
            if subgen_capacity_free(processing_count=processing, arena_in_flight=self._inflight, n=n):
                return
            if not emitted:
                emitted = True
                run.status = "waiting_for_capacity"
                self._store.save(run)
                self._emit(run.id, {"event": "waiting_for_capacity",
                                    "data": {"id": run.id, "ahead": processing}})
            await asyncio.sleep(CAPACITY_POLL_INTERVAL_S)
```

Wire it into `_run` — replace the `async with self._sema:` body opening (lines ~175-177) so the gate runs after the semaphore and the inflight counter brackets the actual work:

```python
            async with self._sema:
                await self._await_capacity(run)
                run.status = "running"
                self._store.save(run)
                self._inflight += 1
                try:
                    self._emit(
                        run_id,
                        {"event": "start",
                         "data": {"id": run.id, "variants": [v["label"] for v in run.variants]}},
                    )
                    await self._execute(run)
                finally:
                    self._inflight -= 1
```

- [ ] **Step 4: Run to verify pass**

Run: `PYTHONPATH=src python -m pytest tests/test_arena_service.py -q`
Expected: PASS (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add src/subarr/arena_service.py tests/test_arena_service.py
git commit -m "feat: arena capacity gate + waiting_for_capacity state + inflight_count"
```

---

## Task 4: Feeder capacity gate

**Files:**
- Modify: `src/subarr/pending_feeder.py` (`__init__` ~64, `tick()` ~149)
- Test: `tests/test_pending_feeder.py`

Add `caps_provider` and `arena_inflight_provider` (defaulting to no-op) and a `_processing_count` helper. In `tick()`, stop feeding when the capacity invariant is closed — alongside the existing `effective < target` reorder cap.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pending_feeder.py  (add; mirror the file's existing fixtures)
import pytest
from subarr.pending_feeder import PendingQueueFeeder
from subarr.pending_queue import PendingQueueStore


class _Subgen:
    def __init__(self, q):
        self._q = q
    async def queue(self):
        return self._q


@pytest.mark.asyncio
async def test_feeder_holds_when_arena_consumes_the_only_slot(tmp_path):
    # N=1, no subgen processing, but an arena sweep is in flight → feeder must
    # NOT submit (would make 2 concurrent on a single-job subgen).
    store = PendingQueueStore(tmp_path / "q.db")
    store.enqueue(canonical_path="/m/a.mkv")
    submitted = []
    async def _submit(job): submitted.append(job)  # feeder awaits submit_job
    feeder = PendingQueueFeeder(
        store=store,
        subgen_provider=lambda: _Subgen({"queued": [], "processing": [],
                                         "queued_count": 0, "processing_count": 0}),
        submit_job=_submit,
        target_depth_provider=lambda: 2,
        caps_provider=lambda: type("C", (), {"concurrent_transcriptions": 1})(),
        arena_inflight_provider=lambda: 1,
    )
    n = await feeder.tick()
    assert n == 0 and submitted == []


@pytest.mark.asyncio
async def test_feeder_unaffected_when_n_none(tmp_path):
    store = PendingQueueStore(tmp_path / "q.db")
    store.enqueue(canonical_path="/m/a.mkv")
    submitted = []
    async def _submit(job): submitted.append(job)
    feeder = PendingQueueFeeder(
        store=store,
        subgen_provider=lambda: _Subgen({"queued": [], "processing": [],
                                         "queued_count": 0, "processing_count": 0}),
        submit_job=_submit,
        target_depth_provider=lambda: 2,
        caps_provider=lambda: type("C", (), {"concurrent_transcriptions": None})(),
        arena_inflight_provider=lambda: 5,
    )
    n = await feeder.tick()
    assert n == 1 and len(submitted) == 1
```

(Note: match the existing test file's `submit_job` shape — it's an `async` callable. Use the file's existing helper if one is defined; the snippets above show both an async and a list-append form — keep the async one to match production.)

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src python -m pytest tests/test_pending_feeder.py -q -k "arena_consumes or n_none"`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'caps_provider'`.

- [ ] **Step 3: Implement**

In `pending_feeder.py`, add a processing-only reader near `_effective_depth`:

```python
def _processing_count(q: dict) -> int:
    p = q.get("processing_count")
    return p if isinstance(p, int) else len(q.get("processing") or [])
```

Extend `__init__` signature (after `paused_provider=lambda: False,`):

```python
        caps_provider=lambda: None,
        arena_inflight_provider=lambda: 0,
```
and store them (after `self._paused = paused_provider`):

```python
        self._caps = caps_provider
        self._arena_inflight = arena_inflight_provider
```

In `tick()`, after computing `effective`/`target` and before the `while effective < target:` loop, compute the gate inputs:

```python
        from .subgen_capacity import subgen_capacity_free
        caps = self._caps()
        n = getattr(caps, "concurrent_transcriptions", None) if caps else None
        processing = _processing_count(q)
```

Change the loop condition so a closed gate stops feeding (each submission consumes a slot, so re-check inside the loop with the running count of this tick's submissions added to `processing`):

```python
        while effective < target and subgen_capacity_free(
            processing_count=processing + submitted, arena_in_flight=self._arena_inflight(), n=n
        ):
            ...  # unchanged body
```

(`submitted` already increments once per job this tick, so it correctly models each just-submitted job occupying a slot.)

- [ ] **Step 4: Run to verify pass**

Run: `PYTHONPATH=src python -m pytest tests/test_pending_feeder.py -q`
Expected: PASS (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add src/subarr/pending_feeder.py tests/test_pending_feeder.py
git commit -m "feat: feeder respects subgen GPU-concurrency budget (arena-aware)"
```

---

## Task 5: Wire providers in app.py

**Files:**
- Modify: `src/subarr/app.py` (arena construction ~372, feeder construction ~456)

No new tests — this is wiring; Task 9's live verify covers it. Keep the change minimal and in-pattern with the existing `lambda: app_.state...` providers.

- [ ] **Step 1: Add providers to the arena**

In the `ArenaService(...)` constructor (~line 372), add (the closures resolve live, and `app_.state.subgen` / `subgen_caps` already exist by this point):

```python
        subgen_provider=lambda: app_.state.subgen,
        caps_provider=lambda: getattr(app_.state, "subgen_caps", None),
```

- [ ] **Step 2: Add providers to the feeder**

In the `PendingQueueFeeder(...)` constructor (~line 456), add:

```python
        caps_provider=lambda: getattr(app_.state, "subgen_caps", None),
        arena_inflight_provider=lambda: app_.state.arena.inflight_count(),
```

(`app_.state.arena` is built ~line 372, before the feeder ~line 456 — order is safe.)

- [ ] **Step 3: Verify import + boot**

Run: `PYTHONPATH=src python -c "import subarr.app"`
Expected: no error.

- [ ] **Step 4: Commit**

```bash
git add src/subarr/app.py
git commit -m "feat: wire subgen-capacity providers into feeder + arena"
```

---

## Task 6: Surface B backend — `arena_active` on GET /api/queue

**Files:**
- Modify: `src/subarr/routers/queue.py` (the `GET /api/queue` handler — locate the final `return {**live, "history": ...}`)
- Test: `tests/test_queue_router.py` (or the existing queue-router test module)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_queue_router.py  (add — use the app fixture this module already uses)
def test_queue_response_includes_arena_active(client, monkeypatch):
    # Arrange the app's arena to report one in-flight sweep, then assert the
    # /api/queue payload surfaces it for the read-only Surface B indicator.
    client.app.state.arena.inflight_count = lambda: 1
    r = client.get("/api/queue")
    assert r.status_code == 200
    assert r.json()["arena_active"] == 1
```

(If the test module builds the app differently, follow its existing pattern for reaching `app.state.arena`; the assertion is the contract: `arena_active` is an int in the response.)

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src python -m pytest tests/test_queue_router.py -q -k arena_active`
Expected: FAIL — `KeyError: 'arena_active'`.

- [ ] **Step 3: Implement**

In `routers/queue.py`, in the `GET /api/queue` handler, compute the count from app state and add it to the response dict (the handler already returns `{**live, "history": history, ...}`):

```python
    arena = getattr(request.app.state, "arena", None)
    arena_active = arena.inflight_count() if arena is not None else 0
```
and add `"arena_active": arena_active,` to the returned dict. (The handler has the FastAPI `Request` — if not, add `request: Request` to its signature; the module already imports `Request` for other routes.)

- [ ] **Step 4: Run to verify pass**

Run: `PYTHONPATH=src python -m pytest tests/test_queue_router.py -q -k arena_active`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/subarr/routers/queue.py tests/test_queue_router.py
git commit -m "feat: GET /api/queue reports arena_active (Surface B data)"
```

---

## Task 7: Surface A frontend — Tuning Lab waiting state

**Files:**
- Modify: `src/subarr/static/v1/home-hifi/arena.jsx` (`StatusPill` map ~line 85; SSE event handling)
- Test: `tests/frontend/` (vitest — mirror an existing arena/component test; if none, a minimal `StatusPill` render test)

- [ ] **Step 1: Write the failing vitest**

```js
// tests/frontend/arena-status.test.jsx  (adapt import paths to the vitest harness)
import { render, screen } from '@testing-library/react';
import { StatusPill } from '../../src/subarr/static/v1/home-hifi/arena.jsx';

test('waiting_for_capacity shows a waiting label', () => {
  render(<StatusPill status="waiting_for_capacity" />);
  expect(screen.getByText(/waiting for subgen/i)).toBeInTheDocument();
});
```

(If `StatusPill` isn't exported, export it for the test — a named export is fine and matches how other components are tested in `tests/frontend/`.)

- [ ] **Step 2: Run to verify failure**

Run: `npm run test -- arena-status` (from `C:\Projects\subarr`)
Expected: FAIL — no matching text / `StatusPill` not exported.

- [ ] **Step 3: Implement**

In `arena.jsx`, add to the `StatusPill` `map` (~line 85):

```js
    waiting_for_capacity: { kind: 'idle', text: 'Waiting for subgen capacity' },
```

In the SSE handler that updates run state on events, ensure a `waiting_for_capacity` event sets the run's status to `'waiting_for_capacity'` (mirror how `'start'`/`'queued'` events map). On the run card, when `run.status === 'waiting_for_capacity'`, render the helper line, e.g.:

```jsx
{run.status === 'waiting_for_capacity' && (
  <div style={{ color: 'var(--fg-3)', fontSize: 'var(--text-sm)' }}>
    Waiting for subgen to free a slot — your subgen runs a limited number of
    transcriptions at once. This starts automatically when one finishes.
  </div>
)}
```

Export `StatusPill` if not already exported.

- [ ] **Step 4: Run to verify pass**

Run: `npm run test -- arena-status`
Expected: PASS.

- [ ] **Step 5: Rebuild the bundle + commit**

```bash
npm run build:frontend
git add src/subarr/static/v1/home-hifi/arena.jsx src/subarr/static/v1/home-hifi/arena.bundle.js tests/frontend/arena-status.test.jsx
git commit -m "feat: Tuning Lab shows waiting_for_capacity state (Surface A)"
```

---

## Task 8: Surface B frontend — Queue awareness indicator

**Files:**
- Modify: `src/subarr/static/v1/home-hifi/queue.jsx` (top of the rendered panel, ~line 72; the component already polls `/api/queue` ~line 25)
- Test: `tests/frontend/queue-arena-indicator.test.jsx`

- [ ] **Step 1: Write the failing vitest**

```js
// tests/frontend/queue-arena-indicator.test.jsx
import { render, screen } from '@testing-library/react';
import { ArenaSlotIndicator } from '../../src/subarr/static/v1/home-hifi/queue.jsx';

test('shows the indicator when a sweep is active', () => {
  render(<ArenaSlotIndicator arenaActive={1} />);
  expect(screen.getByText(/tuning lab sweep running/i)).toBeInTheDocument();
});

test('renders nothing when no sweep is active', () => {
  const { container } = render(<ArenaSlotIndicator arenaActive={0} />);
  expect(container).toBeEmptyDOMElement();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm run test -- queue-arena-indicator`
Expected: FAIL — `ArenaSlotIndicator` not exported.

- [ ] **Step 3: Implement**

In `queue.jsx`, add a small read-only component and render it from the data already polled off `/api/queue` (read `data.arena_active`):

```jsx
export function ArenaSlotIndicator({ arenaActive }) {
  if (!arenaActive) return null;
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px',
      background: 'var(--bg-1)', border: '1px solid var(--bg-3)',
      borderRadius: 'var(--radius-md)', color: 'var(--fg-2)', fontSize: 'var(--text-sm)',
    }}>
      <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--violet-500)' }} />
      Tuning Lab sweep running · using {arenaActive} GPU slot{arenaActive === 1 ? '' : 's'}
    </div>
  );
}
```

Render `<ArenaSlotIndicator arenaActive={data?.arena_active || 0} />` near the top of the Queue panel (~line 72, inside the main `return`).

- [ ] **Step 4: Run to verify pass**

Run: `npm run test -- queue-arena-indicator`
Expected: PASS (2 passed).

- [ ] **Step 5: Rebuild the bundle + commit**

```bash
npm run build:frontend
git add src/subarr/static/v1/home-hifi/queue.jsx src/subarr/static/v1/home-hifi/queue.bundle.js tests/frontend/queue-arena-indicator.test.jsx
git commit -m "feat: Queue page shows arena sweep slot indicator (Surface B)"
```

---

## Task 9: Full verification + no-mock-data gate + PR

**Files:** none (verification only)

- [ ] **Step 1: Full backend suite**

Run: `PYTHONPATH=src python -m pytest -q`
Expected: all pass (the run before this work was 1159 passed, 2 skipped; expect that plus the new tests).

- [ ] **Step 2: Lint**

Run: `python -m ruff check src/subarr/subgen_capacity.py src/subarr/subgen_client.py src/subarr/arena_service.py src/subarr/pending_feeder.py src/subarr/app.py src/subarr/routers/queue.py`
Expected: no errors.

- [ ] **Step 3: Frontend tests + confirm bundles rebuilt**

Run: `npm run test` then `npm run build:frontend`
Expected: green; `git status` shows the two `.bundle.js` already committed (no uncommitted bundle drift — the CI bundle-drift check gates on this).

- [ ] **Step 4: No-mock-data gate (hard requirement)**

Review the full diff for stub/fake/sample values reaching a runtime path:

Run: `git diff origin/main...HEAD -- src/ | grep -inE "mock|fake|stub|sample|dummy|hardcod|TODO|FIXME"`
Expected: matches only in `tests/` context or comments — NONE in a production code path. In particular confirm: no hardcoded `N`, no test-only capability injection in `src/`, the `arena_inflight_provider`/`caps_provider` defaults are safe real defaults (`0` / `None`) not placeholders, and `app.py` passes the real `app_.state.arena.inflight_count` and `subgen_caps` (not a literal).

- [ ] **Step 5: Open the PR**

```bash
git push -u origin feat/arena-subgen-concurrency
gh pr create --title "feat: arena respects subgen GPU-concurrency budget (+ visibility)" \
  --body "Implements docs/superpowers/specs/2026-06-19-arena-subgen-concurrency-design.md. Dormant-safe until subgen r10 publishes concurrent_transcriptions."
```

- [ ] **Step 6: Land after CI green** (`gh pr merge <NN> --squash --admin --delete-branch`).

---

## Piece 1 (separate repo, r10): expose `concurrent_transcriptions`

This is the one subgen-side change. Do it in the `subarr-subgen` repo (its own PR + r10 image build). subarr Piece 2 above is dormant-safe without it, so order is flexible — but the feature only goes live once this ships and `subgen-next` runs the r10 image.

- [ ] **Step 1:** In `subgen.py`, in the `GET /queue` `capabilities` dict (the block that already lists `audio_language_override`, `per_request_kwargs`, …), add:

```python
            # [r10] subarr reads this to size its GPU-concurrency budget so the
            # Tuning-Lab arena's /asr path respects the same limit as the worker
            # pool. The value is the live CONCURRENT_TRANSCRIPTIONS global.
            "concurrent_transcriptions": concurrent_transcriptions,
```

- [ ] **Step 2:** Confirm `concurrent_transcriptions` is in scope where `/queue` is defined (it's a module global set from `os.getenv('CONCURRENT_TRANSCRIPTIONS', 2)`). No other change.

- [ ] **Step 3:** Build/tag r10, deploy to `subgen-next`, then verify on the dev box:

```bash
# subarr reads N live once r10 is running:
wsl docker exec subarr-next python -c "import asyncio; from subarr.subgen_client import SubgenClient; print(asyncio.run(SubgenClient().probe_capabilities()).concurrent_transcriptions)"
```
Expected: prints the integer (e.g. `2`), not `None`.

- [ ] **Step 4:** Live smoke: set `CONCURRENT_TRANSCRIPTIONS=1` on subgen-next, start a queue job, then fire a Tuning-Lab sweep — confirm the sweep shows **Waiting for subgen capacity** (Surface A) and the Queue page shows the **sweep slot indicator** (Surface B) once it starts, and the two never run a 2nd transcription concurrently.

---

## Out of scope (tracked separately — do NOT bundle here)

- **`/batch` `audio_language_override` 3-letter normalization** (`resolve_audio_language_override` in `audio_lang_store.py`) — defensive `normalize_lang`; its own small PR.
- **Hard, race-free enforcement inside subgen** (`/asr` sharing the worker pool's semaphore) — the belt-and-suspenders successor to this soft external gate.
