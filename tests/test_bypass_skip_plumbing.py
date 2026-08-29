"""#458 follow-on: subarr must be able to say "transcribe this one anyway".

After the #458 fix, a file whose only English subtitle is a bitmap correctly
stops counting as covered. But subgen still refuses to transcribe it when
SKIP_IF_AUDIO_LANGUAGES contains its audio language, which is the common case:
these are English-audio files whose English subs are pictures. Measured on a
real library, 128 files sit in exactly that blind spot.

subgen v4.23 adds ?bypass_skip=true for this. These tests pin the plumbing,
because the failure mode is silent: drop the flag anywhere along
  request -> scan_runner.start -> _run -> SubgenClient.batch -> query param
and subarr still returns 202, subgen still returns 200, and the file is still
skipped. The user sees a button that does nothing, with no error to explain it.
"""

from __future__ import annotations

import httpx
import pytest

from subarr.subgen_client import SubgenClient


def _client(handler) -> SubgenClient:
    c = SubgenClient(base_url="http://fake-subgen:9000")
    c._client = httpx.AsyncClient(base_url="http://fake-subgen:9000", transport=httpx.MockTransport(handler))
    return c


# ── the capability must be declared and parsed ──────────────────────────────
def _caps_handler(caps: dict | None):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(200, json={"version": "Subgen 2026.08.1 (docker)"})
        if request.url.path == "/queue":
            body = {
                "queued": [],
                "processing": [],
                "queued_count": 0,
                "processing_count": 0,
                "idle": True,
                "version": "2026.08.1",
            }
            if caps is not None:
                body["capabilities"] = caps
            return httpx.Response(200, json=body)
        return httpx.Response(404)

    return handler


@pytest.mark.asyncio
async def test_bypass_skip_capability_is_read():
    c = _client(_caps_handler({"bypass_skip": True}))
    caps = await c.probe_capabilities()
    await c.aclose()
    assert caps.bypass_skip is True
    assert caps.to_dict()["bypass_skip"] is True


@pytest.mark.asyncio
async def test_bypass_skip_capability_absent_defaults_off():
    # Every subgen before v4.23, including vanilla. Must default OFF so subarr
    # does not offer an action that silently does nothing.
    c = _client(_caps_handler(None))
    caps = await c.probe_capabilities()
    await c.aclose()
    assert caps.bypass_skip is False


# ── the query param must actually reach the wire ────────────────────────────
@pytest.mark.asyncio
async def test_batch_sends_bypass_skip_when_asked():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"walked": 1, "dispatched": 1})

    c = _client(handler)
    await c.batch("/media/TV/x", bypass_skip=True)
    await c.aclose()
    assert seen["params"].get("bypass_skip") == "true"


@pytest.mark.asyncio
async def test_batch_omits_bypass_skip_by_default():
    """Omitted, not sent as false.

    A pre-v4.23 subgen drops an unknown param silently, so sending it is
    harmless there -- but omitting keeps the request identical to what every
    existing caller sends today, which is what makes this change provably
    behaviour-preserving.
    """
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"walked": 1, "dispatched": 1})

    c = _client(handler)
    await c.batch("/media/TV/x")
    await c.aclose()
    assert "bypass_skip" not in seen["params"]


# ── the flag must survive the scan_runner hop ───────────────────────────────
def test_scan_runner_accepts_and_tracks_bypass_skip():
    import inspect

    from subarr.scan_runner import ScanRunner

    assert "bypass_skip" in inspect.signature(ScanRunner.start).parameters
    assert inspect.signature(ScanRunner.start).parameters["bypass_skip"].default is False


def test_coverage_queue_request_exposes_bypass_skip():
    from subarr.routers.coverage_actions import CoverageQueueRequest

    req = CoverageQueueRequest()
    assert req.bypass_skip is False
    assert CoverageQueueRequest(bypass_skip=True).bypass_skip is True


# ── the whole chain, end to end ─────────────────────────────────────────────
def test_pending_job_carries_bypass_skip_through_persistence(tmp_path):
    """It has to survive the DB round trip, not just the request.

    The job is written to SQLite and drained later by the feeder, so a flag that
    is accepted by the API but never persisted would be lost between enqueue and
    submit -- the button appears to work and the file is still skipped.
    """
    from subarr.migrate import run_migrations
    from subarr.pending_queue import PendingQueueStore

    db = tmp_path / "t.db"
    run_migrations(db)
    q = PendingQueueStore(db)
    job = q.enqueue("TV/Show/ep.mkv", source="gaps", bypass_skip=True)
    again = q.get(job.id)
    assert again is not None
    assert again.bypass_skip is True, "flag lost across the DB round trip"
    assert again.to_dict()["bypass_skip"] is True


def test_pending_job_defaults_to_not_bypassing(tmp_path):
    from subarr.migrate import run_migrations
    from subarr.pending_queue import PendingQueueStore

    db = tmp_path / "t.db"
    run_migrations(db)
    q = PendingQueueStore(db)
    job = q.enqueue("TV/Show/ep.mkv", source="gaps")
    assert q.get(job.id).bypass_skip is False


def test_insert_placeholders_match_the_column_list():
    """Pins the drift this change exposed.

    The INSERT had a hardcoded run of 15 '?' against _COLS. Adding a 16th column
    would have thrown at runtime on the first enqueue, not at import, so nothing
    in CI would necessarily have caught it before a user did.
    """
    from subarr.pending_queue import _COLS, _Q_INSERT

    assert _Q_INSERT.count("?") == _COLS.count(",") + 1
