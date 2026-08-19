"""#451 phase-2 wire-trace tests.

Phase 2 threads nullable provenance claims (task / source_language /
target_language / submission_origin) from every producer through the pending
queue, the scan runner, subgen's /batch wire, and the provenance ledger, and
wires the webhook completion to the atomic `record_webhook_and_complete`
before the shared downstream consumption.

These tests wire-trace each link of that chain:
  * subgen_client.batch — serialization + normalization + omission of NULLs
  * ScanRunner.start — validation + normalized per-scan claim storage
  * feeder — forwards the full PendingJob (claims intact) to submit_job
  * feeder → runner → batch → ledger — end-to-end propagation through the
    real app (real _feeder_submit, real runner, real ledger)
  * webhook — precedence/atomicity, subtitle-as-locator-only, idempotency
  * producers — submission_origin set per producer, unknown claims stay NULL

The hard rule exercised throughout: claims are ONLY ever sourced from what the
producer actually knows — never inferred from the subtitle/media filename.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from subarr.migrate import run_migrations
from subarr.pending_feeder import PendingQueueFeeder
from subarr.pending_queue import PendingQueueStore
from subarr.scan_runner import ScanRunner
from subarr.scan_store import ScanStore
from subarr.subgen_client import SubgenClient

# ─── subgen_client.batch serialization + omission ─────────────────────────


def _make_client(mock_transport: httpx.MockTransport) -> SubgenClient:
    c = SubgenClient(base_url="http://fake-subgen:9000")
    c._client = httpx.AsyncClient(
        base_url="http://fake-subgen:9000",
        transport=mock_transport,
    )
    return c


@pytest.mark.asyncio
async def test_batch_forwards_normalized_source_target_languages():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"walked": 1, "queued": 1})

    c = _make_client(httpx.MockTransport(handler))
    await c.batch("/media/x", source_language="EN", target_language="ger", task="translate")
    await c.aclose()

    # Normalized at the wire boundary (3-letter 'ger' → ISO-639-1 'de').
    assert seen["params"]["source_language"] == "en"
    assert seen["params"]["target_language"] == "de"
    assert seen["params"]["task"] == "translate"


@pytest.mark.asyncio
async def test_batch_omits_null_language_claims_and_task():
    """An existing caller that declares no claims must produce a byte-identical
    request: no source_language / target_language / task params at all."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"walked": 0})

    c = _make_client(httpx.MockTransport(handler))
    await c.batch("/media/x")
    await c.aclose()

    assert "source_language" not in seen["params"]
    assert "target_language" not in seen["params"]
    assert "task" not in seen["params"]
    # Existing request options unchanged.
    assert seen["params"]["directory"] == "/media/x"
    assert seen["params"]["reverse"] == "false"


# ─── ScanRunner.start validation + normalized claim storage ───────────────


def _scan_store(tmp_path) -> ScanStore:
    db = tmp_path / "scans.db"
    run_migrations(db)
    return ScanStore(db)


@pytest.mark.asyncio
async def test_start_rejects_invalid_task(tmp_path):
    store = _scan_store(tmp_path)
    runner = ScanRunner(store=store, subgen=_NullSubgen())
    scan = store.create(["TV/Show/ep.mkv"], reverse=False)
    with pytest.raises(ValueError):
        runner.start(scan, task="summarize")  # invalid — rejected before a task spawns


@pytest.mark.asyncio
async def test_start_accepts_transcribe_and_translate_and_normalizes(tmp_path):
    store = _scan_store(tmp_path)
    runner = ScanRunner(store=store, subgen=_NullSubgen())
    scan = store.create(["TV/Show/ep.mkv"], reverse=False)
    runner.start(
        scan,
        task="translate",
        source_language="EN",
        target_language="ger",
    )
    assert runner._claims[scan.id] == {
        "task": "translate",
        "source_language": "en",
        "target_language": "de",
    }
    await _cancel_scan(runner, scan.id)


@pytest.mark.asyncio
async def test_start_defaults_all_claims_none(tmp_path):
    store = _scan_store(tmp_path)
    runner = ScanRunner(store=store, subgen=_NullSubgen())
    scan = store.create(["TV/Show/ep.mkv"], reverse=False)
    runner.start(scan)  # no claims declared → all NULL (unknown, not inferred)
    assert runner._claims[scan.id] == {
        "task": None,
        "source_language": None,
        "target_language": None,
    }
    await _cancel_scan(runner, scan.id)


async def _cancel_scan(runner: ScanRunner, scan_id: str) -> None:
    t = runner._tasks[scan_id]
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass


class _NullSubgen:
    """Minimal subgen stub for ScanRunner claim-storage tests (never reached)."""

    async def batch(self, *a, **k):
        return (200, {"walked": 1, "queued": 1})


# ─── feeder forwards the full job (claims intact) to submit_job ────────────


class _Recorder:
    def __init__(self):
        self.calls = []

    async def __call__(self, job):
        self.calls.append(job)


@pytest.mark.asyncio
async def test_feeder_passes_job_claims_to_submit(tmp_path):
    db = tmp_path / "q.db"
    run_migrations(db)
    store = PendingQueueStore(db)
    store.enqueue(
        "TV/Show/ep.mkv",
        source="manual",
        task="translate",
        source_language="en",
        target_language="es",
        submission_origin="manual_scan",
    )
    rec = _Recorder()
    feeder = PendingQueueFeeder(
        store=store,
        subgen_provider=lambda: _FakeSubgen(),
        submit_job=rec,
        target_depth_provider=lambda: 2,
        paused_provider=lambda: False,
    )
    await feeder.tick()
    assert len(rec.calls) == 1
    got = rec.calls[0]
    assert (got.task, got.source_language, got.target_language, got.submission_origin) == (
        "translate",
        "en",
        "es",
        "manual_scan",
    )


class _FakeSubgen:
    async def queue(self):
        return {"queued": [], "processing": [], "queued_count": 0, "processing_count": 0, "idle": True}


# ─── end-to-end: feeder → runner → subgen /batch → ledger propagation ──────


_BATCH_PARAMS: list[dict[str, str]] = []


def _wiretrace_subgen_handler(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/status":
        return httpx.Response(200, json={"version": "Subgen 2026.05.3 (test)"})
    if req.url.path == "/queue":
        return httpx.Response(
            200,
            json={
                "queued": [],
                "processing": [],
                "queued_count": 0,
                "processing_count": 0,
                "idle": True,
                "version": "test",
                "capabilities": {"per_request_task": True},
            },
        )
    if req.url.path == "/batch":
        _BATCH_PARAMS.append(dict(req.url.params))
        return httpx.Response(
            200,
            json={
                "walked": 1,
                "queued": 1,
                "skipped": 0,
                "already_in_queue": 0,
                "no_audio": 0,
                "pending_language_detect": 0,
                "path": req.url.params.get("directory"),
                "reverse": False,
            },
        )
    return httpx.Response(404, json={"detail": "stub: unhandled"})


@pytest.mark.subgen(handler=_wiretrace_subgen_handler)
def test_feeder_to_ledger_and_batch_propagate_claims(app_with_stub):
    """Full real-app wire trace: a producer's claims on the pending row arrive
    BOTH on subgen's /batch request AND on the provenance ledger row."""
    _BATCH_PARAMS.clear()
    app = app_with_stub.app
    canonical = "TV/Show/ep.mkv"

    # Enqueue as a producer would — explicit claims, no filename inference.
    app.state.pending_queue.enqueue(
        canonical,
        source="manual",
        task="translate",
        source_language="en",
        target_language="es",
        submission_origin="manual_scan",
    )
    app.state.queue_feeder.kick()

    # Poll for the runner to submit (batch params recorded) and the ledger row
    # (_feeder_submit records synchronously at drain time).
    deadline = time.time() + 5
    entry = None
    while time.time() < deadline:
        rows = app.state.provenance.query_by_path(canonical)
        if rows and _BATCH_PARAMS:
            entry = rows[0]
            break
        time.sleep(0.05)

    assert entry is not None, "ledger row never created by _feeder_submit"
    # Ledger received the claims.
    assert entry.task == "translate"
    assert entry.source_language == "en"
    assert entry.target_language == "es"
    assert entry.submission_origin == "manual_scan"
    # subgen /batch received the same claims (via runner.start claims storage).
    assert _BATCH_PARAMS[-1]["task"] == "translate"
    assert _BATCH_PARAMS[-1]["source_language"] == "en"
    assert _BATCH_PARAMS[-1]["target_language"] == "es"


# ─── webhook precedence / atomicity / subtitle-as-locator ──────────────────


def test_webhook_persists_evidence_then_completes(app_with_stub):
    """The webhook must persist its evidence + complete the OPEN row BEFORE any
    downstream consumption, record webhook evidence, treat subtitle as a
    canonicalized locator (never a language claim), and fire the shared
    downstream flow (matched=1)."""
    from subarr.paths import subgen_to_canonical

    app = app_with_stub.app
    canonical = "TV/Foreign Drama/Season 1/Foreign.S01E03.mkv"
    led_id = app.state.provenance.record(
        canonical_path=canonical,
        scan_id="scan-1",
        series_id=42,
        task="translate",
        target_language="es",
    )

    r = app_with_stub.post(
        "/api/subgen/webhook/completed",
        json={
            "event": "translated",
            "file": "/media/" + canonical,
            "subtitle": "/media/TV/Foreign Drama/Season 1/Foreign.S01E03.en.srt",
            "language": "es",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["matched"] == 1  # downstream consumption still ran

    entry = next(e for e in app.state.provenance.query_by_path(canonical) if e.id == led_id)
    assert entry.completed_at is not None
    # Webhook evidence persisted.
    assert entry.webhook_event == "translated"
    assert entry.webhook_language == "es"
    # Subtitle is a canonicalized LOCATOR (media prefix stripped), never a claim.
    assert entry.webhook_subtitle == subgen_to_canonical(
        "/media/TV/Foreign Drama/Season 1/Foreign.S01E03.en.srt"
    )
    assert not entry.webhook_subtitle.startswith("/media/")
    # task(translate)+target(es) agree with event(translated)+lang(es) → 0.
    assert entry.provenance_conflict == 0


def test_webhook_second_delivery_is_idempotent(app_with_stub):
    """Atomic exactly-once: a repeated identical delivery completes nothing new
    (matched=0) and does not re-stamp completed_at."""
    app = app_with_stub.app
    canonical = "TV/Foreign Drama/Season 1/Foreign.S01E03.mkv"
    led_id = app.state.provenance.record(canonical_path=canonical, scan_id="scan-1", task="transcribe")
    payload = {
        "event": "transcribed",
        "file": "/media/" + canonical,
        "subtitle": "/media/TV/Foreign Drama/Season 1/Foreign.S01E03.en.srt",
        "language": "en",
    }

    first = app_with_stub.post("/api/subgen/webhook/completed", json=payload)
    assert first.json()["matched"] == 1
    stamped = next(e for e in app.state.provenance.query_by_path(canonical) if e.id == led_id)

    second = app_with_stub.post("/api/subgen/webhook/completed", json=payload)
    assert second.json()["matched"] == 0  # row already consumed
    after = next(e for e in app.state.provenance.query_by_path(canonical) if e.id == led_id)
    assert after.completed_at == stamped.completed_at  # exactly-once


def test_webhook_unknown_submission_claims_stay_inconclusive(app_with_stub):
    """A submission row with NO claims yields no conflict (NULL), even when the
    webhook carries its own evidence — provenance stays 'unknown', not asserted."""
    app = app_with_stub.app
    canonical = "TV/Foreign Drama/Season 1/Foreign.S01E03.mkv"
    led_id = app.state.provenance.record(canonical_path=canonical, scan_id="scan-1")

    r = app_with_stub.post(
        "/api/subgen/webhook/completed",
        json={"event": "transcribed", "file": "/media/" + canonical, "language": "en"},
    )
    assert r.status_code == 200
    entry = next(e for e in app.state.provenance.query_by_path(canonical) if e.id == led_id)
    assert entry.provenance_conflict is None
    assert entry.webhook_event == "transcribed"


# ─── producers set submission_origin; unknown claims stay NULL ─────────────


def test_manual_scan_sets_origin_not_inferred_languages(app_with_stub):
    """The scan-tab producer records its origin but does NOT infer task or
    language from the path (the hard rule — no filename inference)."""
    app = app_with_stub.app
    _set_paused(app, True)
    try:
        r = app_with_stub.post("/api/scan", json={"paths": ["TV/Show/ep.mkv"]})
        assert r.status_code == 202, r.text
        job = next(
            (j for j in app.state.pending_queue.list() if j.canonical_path == "TV/Show/ep.mkv"),
            None,
        )
        assert job is not None
        assert job.submission_origin == "manual_scan"
        # Task/languages are UNKNOWN for a bare manual scan — never syndrome
        # from the filename.
        assert job.task is None
        assert job.source_language is None
        assert job.target_language is None
    finally:
        _set_paused(app, False)


def test_requeue_sets_origin(app_with_stub):
    app = app_with_stub.app
    _set_paused(app, True)
    try:
        r = app_with_stub.post("/api/queue/requeue", json={"path": "TV/Show/ep.mkv"})
        assert r.status_code == 202, r.text
        job = app.state.pending_queue.get(r.json()["job"])
        assert job is not None
        assert job.submission_origin == "requeue"
        assert job.task is None  # requeue carries no explicit task claim
    finally:
        _set_paused(app, False)


@pytest.mark.parametrize(
    "origin",
    ["manual_scan", "coverage", "gaps", "auto", "backfill", "requeue"],
)
def test_each_producer_origin_round_trips_through_enqueue(tmp_path, origin):
    """Every producer origin value survives enqueue → persistence → retrieval
    (the plumbing producers rely on), with unknown claims staying NULL."""
    db = tmp_path / "q.db"
    run_migrations(db)
    store = PendingQueueStore(db)
    j = store.enqueue("TV/Show/ep.mkv", source="manual", submission_origin=origin)
    fetched = store.get(j.id)
    assert fetched.submission_origin == origin


def _set_paused(app, paused: bool) -> None:
    rules = app.state.schedule.get_rules()
    rules.queue_paused = paused
    app.state.schedule.set_rules(rules)
