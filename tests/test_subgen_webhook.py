"""Tests for the push-based subgen completion webhook (#87).

POST /api/subgen/webhook/completed consumes subgen's WEBHOOK_URL_COMPLETED
POSTs and runs the SAME completion flow the polling watcher uses (mark
provenance completed → Bazarr write-back → Plex partial-scan). Polling
stays as the fallback; this is the low-latency push alternative.

Payload shape (verified against subgen_patched.send_completion_webhook):
  { "event": "transcribed", "file": "/media/TV/Show/file.mkv",
    "subtitle": "/media/TV/Show/file.en.srt", "language": "en" }
"""

from __future__ import annotations

import httpx
import pytest


def _bazarr_tasks_handler(req: httpx.Request) -> httpx.Response:
    """Bazarr stub that exposes the scan-disk task + records triggers."""
    path = req.url.path
    if path == "/api/system/tasks" and req.method == "GET":
        return httpx.Response(
            200,
            json={
                "data": [
                    {"job_id": "series_full_scan_subtitles", "name": "Index All Existing Episodes Subtitles"},
                ]
            },
        )
    if path == "/api/system/tasks" and req.method == "POST":
        # trigger_task — record the taskid so the test can assert it fired.
        _bazarr_tasks_handler.triggered.append(req.url.params.get("taskid"))
        return httpx.Response(204)
    return httpx.Response(404, json={"detail": "stub: unhandled"})


_bazarr_tasks_handler.triggered = []  # type: ignore[attr-defined]


@pytest.mark.integrations_stub(bazarr_handler=_bazarr_tasks_handler)
def test_webhook_marks_completed_and_triggers_flow(app_with_stub):
    """A completed-webhook POST marks the matching provenance entry
    completed AND fires the Bazarr scan-disk task (the shared completion
    flow), proving the push path reuses the watcher logic."""
    _bazarr_tasks_handler.triggered.clear()
    app = app_with_stub.app

    # Seed a pending provenance entry (as a scan submission would).
    canonical = "TV/Foreign Drama/Season 1/Foreign.S01E03.mkv"
    led_id = app.state.provenance.record(
        canonical_path=canonical,
        scan_id="scan-1",
        series_id=42,
    )
    pending = app.state.provenance.query_by_path(canonical)
    assert pending and pending[0].completed_at is None

    # subgen reports completion at ITS mount path (/media prefix).
    r = app_with_stub.post(
        "/api/subgen/webhook/completed",
        json={
            "event": "transcribed",
            "file": "/media/TV/Foreign Drama/Season 1/Foreign.S01E03.mkv",
            "subtitle": "/media/TV/Foreign Drama/Season 1/Foreign.S01E03.en.srt",
            "language": "en",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accepted"] is True
    assert body["matched"] == 1
    assert body["canonical_path"] == canonical

    # Provenance entry is now completed.
    after = app.state.provenance.query_by_path(canonical)
    assert after[0].id == led_id
    assert after[0].completed_at is not None

    # The shared completion flow fired Bazarr's scan-disk task (no .srt
    # sidecar on disk → upload path falls through to scan-disk trigger).
    assert "series_full_scan_subtitles" in _bazarr_tasks_handler.triggered


def test_webhook_unknown_path_is_benign_noop(app_with_stub):
    """A completion for a path subarr never tracked (e.g. a subgen Plex
    auto-transcribe) returns matched=0 without error — not every subgen
    job originated from subarr."""
    r = app_with_stub.post(
        "/api/subgen/webhook/completed",
        json={
            "event": "transcribed",
            "file": "/media/TV/Untracked/random.mkv",
            "subtitle": "/media/TV/Untracked/random.en.srt",
            "language": "en",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["matched"] == 0


def test_webhook_missing_file_is_400(app_with_stub):
    r = app_with_stub.post(
        "/api/subgen/webhook/completed",
        json={
            "event": "transcribed",
            "language": "en",
        },
    )
    assert r.status_code == 400


def test_webhook_disabled_rejects_push(subarr_env, monkeypatch):
    """When SUBARR_SUBGEN_WEBHOOK_ENABLED=0 the receiver accepts the POST
    (so subgen doesn't log a delivery failure) but does nothing — polling
    is the operator's chosen driver."""
    monkeypatch.setenv("SUBARR_SUBGEN_WEBHOOK_ENABLED", "0")
    # Rebuild settings so the env change takes effect for this test.
    from subarr import config

    monkeypatch.setattr(config, "settings", config.load())

    from fastapi.testclient import TestClient
    from subarr.app import app

    with TestClient(app) as c:
        canonical = "TV/Foreign Drama/Season 1/Foreign.S01E03.mkv"
        c.app.state.provenance.record(
            canonical_path=canonical,
            scan_id="scan-x",
            series_id=42,
        )
        r = c.post(
            "/api/subgen/webhook/completed",
            json={
                "event": "transcribed",
                "file": "/media/" + canonical,
            },
        )
        assert r.status_code == 200
        assert r.json()["accepted"] is False
        # Entry stays pending — push was ignored.
        after = c.app.state.provenance.query_by_path(canonical)
        assert after[0].completed_at is None
