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


def test_get_forced_segment_exposes_toggle_fields(app_with_stub):
    body = app_with_stub.get("/api/forced-segment").json()
    assert {"enabled", "env_controlled", "vad_available"} <= body.keys()
    assert "state" in body and "summary" in body
    assert isinstance(body["vad_available"], bool)


def test_config_toggle_persists_and_live_applies(app_with_stub, tmp_path, monkeypatch):
    from subarr import config, config_store as cs

    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "ov.json"))
    monkeypatch.delenv("SUBARR_FORCED_SEGMENT_ENABLED", raising=False)
    prior = config.settings.forced_segment_enabled
    try:
        r = app_with_stub.post("/api/forced-segment/config", json={"enabled": True})
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is True
        assert body["env_controlled"] is False
        assert config.settings.forced_segment_enabled is True
        assert cs.load_overrides().get("forced_segment_enabled") is True
    finally:
        object.__setattr__(config.settings, "forced_segment_enabled", prior)


def test_config_toggle_env_pinned_persists_but_env_wins(app_with_stub, tmp_path, monkeypatch):
    from subarr import config, config_store as cs

    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "ov.json"))
    monkeypatch.setenv("SUBARR_FORCED_SEGMENT_ENABLED", "0")
    prior = config.settings.forced_segment_enabled
    try:
        r = app_with_stub.post("/api/forced-segment/config", json={"enabled": True})
        assert r.status_code == 200
        assert r.json()["env_controlled"] is True
        assert config.settings.forced_segment_enabled == prior
        assert cs.load_overrides().get("forced_segment_enabled") is True
    finally:
        object.__setattr__(config.settings, "forced_segment_enabled", prior)


def test_get_vad_available_reflects_vad(app_with_stub, monkeypatch):
    from subarr import vad

    monkeypatch.setattr(vad, "vad_available", lambda: True)
    assert app_with_stub.get("/api/forced-segment").json()["vad_available"] is True
