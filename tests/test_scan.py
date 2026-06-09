"""Tests for POST /api/scan + GET /api/scan/{id} + SSE events."""

from __future__ import annotations

import json
import time

import httpx
import pytest


def _wait_done(client, scan_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/scan/{scan_id}")
        assert r.status_code == 200
        body = r.json()
        if body["status"] in {"done", "error"}:
            return body
        time.sleep(0.05)
    raise AssertionError(f"scan {scan_id} did not finish within {timeout}s")


def test_scan_rejects_unknown_path(app_with_stub):
    r = app_with_stub.post("/api/scan", json={"paths": ["TV/NotARealShow"]})
    assert r.status_code == 404


def test_scan_accepts_individual_file_path(app_with_stub):
    """The Scan tab's file-leaf checkboxes pass an individual .mkv path
    rather than a directory. Subgen's v4.1 transcribe_existing has the
    `if os.path.isfile(path)` branch so this works end-to-end."""
    r = app_with_stub.post("/api/scan", json={"paths": ["TV/Show/ep.mkv"]})
    assert r.status_code == 202
    scan_id = r.json()["id"]
    final = _wait_done(app_with_stub, scan_id)
    assert final["status"] == "done"
    assert final["paths"] == ["TV/Show/ep.mkv"]


def test_scan_rejects_traversal(app_with_stub):
    r = app_with_stub.post("/api/scan", json={"paths": ["../etc"]})
    assert r.status_code == 400


def test_scan_happy_path(app_with_stub):
    r = app_with_stub.post("/api/scan", json={"paths": ["TV/Show"], "reverse": True})
    assert r.status_code == 202
    scan = r.json()
    assert scan["status"] == "pending"
    assert scan["reverse"] is True

    final = _wait_done(app_with_stub, scan["id"])
    assert final["status"] == "done"
    assert len(final["results"]) == 1
    assert final["results"][0]["status"] == "ok"
    assert final["results"][0]["subgen_status_code"] == 200
    assert final["results"][0]["subgen_body"]["walked"] == 1


def _empty_handler(req: httpx.Request) -> httpx.Response:
    # /status + /queue must respond so the capability probe sees a
    # subarr-subgen build. The test point is /batch behaviour.
    if req.url.path == "/status":
        return httpx.Response(
            200,
            json={
                "version": "Subgen 2026.05.3, stable-ts 0.7.0, faster-whisper 1.0.3 (test)",
            },
        )
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
            },
        )
    if req.url.path == "/batch":
        return httpx.Response(
            404,
            json={
                "walked": 0,
                "queued": 0,
                "skipped": 0,
                "already_in_queue": 0,
                "no_audio": 0,
                "pending_language_detect": 0,
                "path": req.url.params.get("directory"),
                "reverse": False,
            },
        )
    return httpx.Response(404)


@pytest.mark.subgen(handler=_empty_handler)
def test_scan_marks_empty_when_subgen_returns_404(app_with_stub):
    r = app_with_stub.post("/api/scan", json={"paths": ["TV/Show"]})
    final = _wait_done(app_with_stub, r.json()["id"])
    assert final["status"] == "done"
    assert final["results"][0]["status"] == "empty"


def test_scan_status_unknown_returns_404(app_with_stub):
    assert app_with_stub.get("/api/scan/does-not-exist").status_code == 404


def test_scan_sse_emits_events_until_done(app_with_stub):
    """Stream should connect, snapshot the scan state, and close cleanly once
    the scan is in a terminal state. If the snapshot already shows 'done' the
    stream returns after one event — that's correct behaviour, not a bug."""
    r = app_with_stub.post("/api/scan", json={"paths": ["TV/Show"]})
    scan_id = r.json()["id"]
    _wait_done(app_with_stub, scan_id)

    with app_with_stub.stream("GET", f"/api/scan/{scan_id}/events") as resp:
        assert resp.status_code == 200
        events: list[tuple[str, dict]] = []
        current_event = None
        for chunk in resp.iter_lines():
            if not chunk:
                continue
            if chunk.startswith("event:"):
                current_event = chunk.split(":", 1)[1].strip()
            elif chunk.startswith("data:"):
                payload = json.loads(chunk.split(":", 1)[1].strip())
                events.append((current_event, payload))

    assert events, "SSE stream produced no events"
    snapshot_event, snapshot_data = events[0]
    assert snapshot_event == "snapshot"
    assert snapshot_data["id"] == scan_id
    assert snapshot_data["status"] == "done"
    assert snapshot_data["results"][0]["status"] == "ok"
