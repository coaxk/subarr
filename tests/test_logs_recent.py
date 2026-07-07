"""#157 gap-fill: /api/logs/recent (snapshot) + /api/logs/subarr/events (SSE).
The /recent tests are sync TestClient tests (app_with_stub) exercised through
the running app. The SSE tail test is async and calls the endpoint coroutine
directly (Starlette 1.2.0 TestClient.stream() deadlocks on close of an infinite
SSE generator — a harness limitation, not an endpoint bug; see that test)."""

from __future__ import annotations

import asyncio
import logging

import pytest


def test_recent_returns_seeded_records_filtered_by_level(app_with_stub):
    # The LogRing is installed on the root logger at import; emit through it.
    logging.getLogger("subarr.test").info("an info line for recent")
    logging.getLogger("subarr.test").warning("a warning line for recent")
    logging.getLogger("subarr.test").error("an error line for recent")

    r = app_with_stub.get("/api/logs/recent?level=WARNING&limit=200")
    assert r.status_code == 200
    body = r.json()
    assert "records" in body
    msgs = [rec["message"] for rec in body["records"]]
    assert "a warning line for recent" in msgs
    assert "an error line for recent" in msgs
    assert "an info line for recent" not in msgs  # filtered out below WARNING
    for rec in body["records"]:
        assert set(rec) >= {"ts", "level", "logger_name", "message", "exc_text"}


def test_recent_default_no_level_returns_all(app_with_stub):
    logging.getLogger("subarr.test").info("unfiltered info line")
    r = app_with_stub.get("/api/logs/recent?limit=500")
    assert r.status_code == 200
    msgs = [rec["message"] for rec in r.json()["records"]]
    assert "unfiltered info line" in msgs


def test_recent_tolerates_missing_ring(app_with_stub, monkeypatch):
    # If the app has no ring wired, the endpoint returns an empty list, not 500.
    import subarr.app as app_mod

    monkeypatch.setattr(app_mod, "LOG_RING", None, raising=False)
    r = app_with_stub.get("/api/logs/recent")
    assert r.status_code == 200
    assert r.json()["records"] == []


@pytest.mark.asyncio
async def test_subarr_events_sse_opens_and_replays_tail(app_with_stub):
    # Smoke-test the live SSE tail. NOTE: the endpoint is an INFINITE generator
    # (replay burst -> live loop), and Starlette 1.2.0's TestClient.stream()
    # deadlocks on close waiting for such a generator to exhaust (verified: a
    # finite body closes, an infinite one hangs regardless of is_disconnected /
    # CancelledError handling). Real ASGI servers cancel the task on
    # http.disconnect, so the endpoint is correct in production; only the test
    # harness can't drive an early close. So we call the endpoint coroutine
    # directly, pull the replayed first chunk under a timeout, then aclose() the
    # body iterator (which runs the generator's finally: ring.unsubscribe).
    import subarr.app as app_mod
    from subarr.routers.logs import logs_subarr_events

    logging.getLogger("subarr.test").warning("tail replay line")

    class _Req:
        async def is_disconnected(self):
            return False

    # The replay burst is exactly the snapshot(limit=tail) records; pull that
    # many chunks so we consume the whole FINITE burst without ever entering the
    # infinite live-wait loop (which would hang). Cancelling a StreamingResponse
    # body_iterator mid-__anext__ breaks it, so we bound the pulls instead.
    ring = app_mod.LOG_RING
    burst_len = len(ring.snapshot(limit=200))
    resp = await logs_subarr_events(_Req(), tail=200)  # type: ignore[arg-type]
    assert resp.media_type == "text/event-stream"
    it = resp.body_iterator
    chunks = []
    for _ in range(burst_len):
        chunks.append(await asyncio.wait_for(it.__anext__(), timeout=5.0))
    # First chunk proves the subgen-shaped SSE framing; our seeded line proves
    # the replay carries the ring's OWN records.
    assert "event: log" in chunks[0]
    assert any("tail replay line" in c for c in chunks)
    await it.aclose()  # cancels the generator -> runs finally: ring.unsubscribe
