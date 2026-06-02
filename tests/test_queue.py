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


def _one_queued_handler(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/queue":
        return httpx.Response(200, json={
            "queued": [{"path": "/media/library/TV/Dup/dupfile.mkv", "type": "transcribe"}],
            "processing": [],
            "queued_count": 1,
            "processing_count": 0,
            "idle": False,
            "version": "2026.05.3",
        })
    return httpx.Response(404)


@pytest.mark.subgen(handler=_one_queued_handler)
def test_inflight_queued_path_not_duplicated_in_history(app_with_stub):
    """A just-submitted file is live in subgen's queue AND has an OK
    ('subgen queued 1') scan_store row. It must appear ONLY in the live
    queued section, not also in history/Recently-done. Regression for the
    double-listing where 40+ queued files showed in both places."""
    from subarr.scan_store import PATH_STATUS_OK
    c = app_with_stub
    store = c.app.state.scans
    scan = store.create(["TV/Dup/dupfile.mkv"], reverse=False)
    scan.results[0].status = PATH_STATUS_OK
    scan.results[0].subgen_body = {"walked": 1, "queued": 1}
    store.save(scan)

    body = c.get("/api/queue").json()
    assert any("dupfile.mkv" in t["path"] for t in body["queued"])
    assert not any("dupfile.mkv" in h["path"] for h in body["history"]), \
        "in-flight queued path must not also appear in history"


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


# ── #queue-cancel: subgen-space path must pass through verbatim ──────────
# Regression: the cancel endpoint used to strip("/") + canonicalise the
# path as if it were subarr-relative. But /api/queue returns subgen's OWN
# path space (e.g. /media/...), and subgen matches its queue by exact path
# — stripping the leading slash made every cancel fail "not in queue".

_CANCEL_SEEN = {}


def _cancel_handler(req):
    if req.url.path == "/queue/cancel":
        _CANCEL_SEEN["path"] = req.url.params.get("path")
        return httpx.Response(200, json={"cancelled": True, "path": _CANCEL_SEEN["path"]})
    # keep boot + GET /queue happy
    return httpx.Response(200, json={"active": None, "queue": []})


@pytest.mark.subgen(handler=_cancel_handler)
def test_cancel_passes_subgen_path_verbatim(app_with_stub):
    import dataclasses
    from subarr.subgen_client import SubgenCapabilities
    c = app_with_stub
    base = c.app.state.subgen_caps or SubgenCapabilities()
    c.app.state.subgen_caps = dataclasses.replace(base, queue_cancel=True)
    _CANCEL_SEEN.clear()

    full = "/media/TV/Innato/Season 1/Innate - S01E06 - x.mkv"
    r = c.post("/api/queue/cancel", json={"path": full})
    assert r.status_code == 200, r.text
    assert r.json().get("cancelled") is True
    # The exact subgen path (leading slash + all) must reach subgen.
    assert _CANCEL_SEEN["path"] == full
