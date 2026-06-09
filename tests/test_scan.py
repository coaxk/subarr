"""POST /api/scan — since #169 this routes submissions through the pending
queue (manual priority) instead of submitting to subgen immediately, so manual
submits are governed by the same advanced queue as everything else. The /{id}
status + SSE endpoints remain for feeder-created scans (no longer returned to
manual callers — they watch the Queue page).
"""

from __future__ import annotations

import time

from subarr.pending_queue import STATUS_PENDING, STATUS_SUBMITTED


def test_scan_rejects_unknown_path(app_with_stub):
    r = app_with_stub.post("/api/scan", json={"paths": ["TV/NotARealShow"]})
    assert r.status_code == 404


def test_scan_rejects_traversal(app_with_stub):
    r = app_with_stub.post("/api/scan", json={"paths": ["../etc"]})
    assert r.status_code == 400


def test_scan_rejects_empty_path(app_with_stub):
    r = app_with_stub.post("/api/scan", json={"paths": [" "]})
    assert r.status_code == 400


def test_scan_routes_to_pending_not_immediate(app_with_stub):
    """A valid manual submit is enqueued (source=manual), not run immediately.
    The response is the new pending contract — no scan_id."""
    r = app_with_stub.post("/api/scan", json={"paths": ["TV/Show/ep.mkv"]})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "pending"
    assert body["enqueued"] == ["TV/Show/ep.mkv"]
    assert body["count"] == 1
    assert "id" not in body  # no immediate scan_id any more
    # it really landed in subarr's queue (pending or already drained → submitted)
    assert "TV/Show/ep.mkv" in app_with_stub.app.state.pending_queue.active_paths()


def _set_paused(app, paused: bool) -> None:
    rules = app.state.schedule.get_rules()
    rules.queue_paused = paused
    app.state.schedule.set_rules(rules)


def test_scan_enqueues_as_manual_priority(app_with_stub):
    """Manual is the top priority bucket so it drains ahead of gaps/backfill."""
    _set_paused(app_with_stub.app, True)  # hold the feeder so the row stays pending
    try:
        app_with_stub.post("/api/scan", json={"paths": ["TV/Show/ep.mkv"]})
        jobs = app_with_stub.app.state.pending_queue.list(status=STATUS_PENDING)
        ours = [j for j in jobs if j.canonical_path == "TV/Show/ep.mkv"]
        assert ours and ours[0].source == "manual"
    finally:
        _set_paused(app_with_stub.app, False)


def test_scan_multiple_paths_all_enqueued(app_with_stub):
    r = app_with_stub.post("/api/scan", json={"paths": ["TV/Show", "TV/Show/ep.mkv"]})
    assert r.status_code == 202
    assert r.json()["count"] == 2
    active = app_with_stub.app.state.pending_queue.active_paths()
    assert {"TV/Show", "TV/Show/ep.mkv"} <= active


def test_scan_flows_through_feeder_to_subgen(app_with_stub):
    """End-to-end: the kicked feeder picks the manual job up and submits it."""
    r = app_with_stub.post("/api/scan", json={"paths": ["TV/Show/ep.mkv"]})
    job_id = r.json()["jobs"][0]
    pending = app_with_stub.app.state.pending_queue
    deadline = time.time() + 5
    while time.time() < deadline:
        job = pending.get(job_id)
        if job and job.status == STATUS_SUBMITTED:
            break
        time.sleep(0.05)
    assert pending.get(job_id).status == STATUS_SUBMITTED


def test_scan_status_unknown_returns_404(app_with_stub):
    assert app_with_stub.get("/api/scan/does-not-exist").status_code == 404
