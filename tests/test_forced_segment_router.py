"""#364 slice 1 — walker control router. Endpoints exist and validate scope;
start/stop/get return the walker state shape. SYNC TestClient (app_with_stub)."""

from __future__ import annotations


def test_get_forced_segment_status(app_with_stub):
    r = app_with_stub.get("/api/forced-segment")
    assert r.status_code == 200
    body = r.json()
    assert "state" in body and "summary" in body


def test_start_rejects_bad_scope(app_with_stub):
    r = app_with_stub.post("/api/forced-segment/start", params={"scope": "bogus"})
    assert r.status_code == 400


def test_start_and_stop(app_with_stub):
    r = app_with_stub.post("/api/forced-segment/start", params={"scope": "library"})
    assert r.status_code == 202
    assert r.json()["state"]["status"] in ("running", "done")
    r2 = app_with_stub.post("/api/forced-segment/stop")
    assert r2.status_code == 200


def test_app_registers_forced_segment_roster_and_walker(app_with_stub):
    # App-wiring assertions: the Health roster carries "forced-segment", the
    # walker + store + generator are on app.state, and the completion watcher
    # has the generator wired for the at-import hook.
    app = app_with_stub.app
    th = app.state.task_health
    names = {t.task_name for t in th.states()}
    assert "forced-segment" in names
    assert getattr(app.state, "forced_segment", None) is not None
    assert getattr(app.state, "forced_segment_store", None) is not None
    assert getattr(app.state, "forced_segment_gen", None) is not None
    assert app.state.watcher._forced_segment is app.state.forced_segment_gen
