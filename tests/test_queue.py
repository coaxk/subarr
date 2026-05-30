"""Tests for GET /api/queue (subgen v4.2 proxy)."""
from __future__ import annotations

import httpx
import pytest


def test_queue_idle_default(app_with_stub):
    r = app_with_stub.get("/api/queue")
    assert r.status_code == 200
    body = r.json()
    assert body["idle"] is True
    assert body["queued"] == []
    assert body["processing"] == []
    assert body["queued_count"] == 0


def _busy_handler(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/queue":
        return httpx.Response(200, json={
            "queued": [{"path": "/media/library/TV/A/file2.mkv", "type": "transcribe"}],
            "processing": [{"path": "/media/library/TV/A/file1.mkv", "type": "transcribe"}],
            "queued_count": 1,
            "processing_count": 1,
            "idle": False,
            "version": "2026.05.3",
        })
    return httpx.Response(404)


@pytest.mark.subgen(handler=_busy_handler)
def test_queue_busy(app_with_stub):
    r = app_with_stub.get("/api/queue")
    assert r.status_code == 200
    body = r.json()
    assert body["idle"] is False
    assert body["queued_count"] == 1
    assert body["processing"][0]["type"] == "transcribe"


def _down_handler(req: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("subgen unreachable", request=req)


@pytest.mark.subgen(handler=_down_handler)
def test_queue_history_only_when_subgen_down(app_with_stub):
    # v1.1.1 Featured Queue: when subgen is unreachable we no longer 503.
    # The queue endpoint serves a history-only view so the user can still
    # inspect/requeue/clean up past submissions while subgen is down.
    # The subgen failure is surfaced via the `subgen_error` field.
    r = app_with_stub.get("/api/queue")
    assert r.status_code == 200
    body = r.json()
    assert body["processing"] == []
    assert body["queued"] == []
    assert "subgen_error" in body
    assert "subgen" in body["subgen_error"].lower()
    assert "history" in body
    assert "history_counts" in body
