"""#169: /api/queue/requeue routes through the pending queue (manual priority),
preserving the #229 audio_language_override by carrying it onto the pending row
for the feeder to apply at submit time — rather than submitting immediately.
"""

from __future__ import annotations


def _set_paused(app, paused: bool) -> None:
    rules = app.state.schedule.get_rules()
    rules.queue_paused = paused
    app.state.schedule.set_rules(rules)


def test_requeue_routes_to_pending_not_immediate(app_with_stub):
    r = app_with_stub.post("/api/queue/requeue", json={"path": "TV/Show/ep.mkv"})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "pending"
    assert body["path"] == "TV/Show/ep.mkv"
    assert "id" not in body  # no immediate scan_id any more
    assert "TV/Show/ep.mkv" in app_with_stub.app.state.pending_queue.active_paths()


def test_requeue_carries_audio_language_override(app_with_stub):
    """#229 must survive the reroute: a user-verified foreign audio language
    rides onto the pending row so the feeder forwards it to subgen."""
    app = app_with_stub.app
    app.state.audio_lang.upsert(
        canonical_path="TV/Show/ep.mkv", lang_code="fra", source="user", confidence=1.0
    )
    _set_paused(app, True)  # hold the feeder so we can inspect the pending row
    try:
        r = app_with_stub.post("/api/queue/requeue", json={"path": "TV/Show/ep.mkv"})
        job = app.state.pending_queue.get(r.json()["job"])
        assert job is not None
        assert job.source == "manual"
        # #358: store normalizes 'fra'→'fr'; subgen resolves the 2-letter form.
        assert job.audio_language_override == "fr"
    finally:
        _set_paused(app, False)


def test_requeue_rejects_missing_path(app_with_stub):
    r = app_with_stub.post("/api/queue/requeue", json={"path": "  "})
    assert r.status_code == 400


def test_requeue_rejects_unknown_file(app_with_stub):
    r = app_with_stub.post("/api/queue/requeue", json={"path": "TV/Nope/missing.mkv"})
    assert r.status_code == 404
