"""#111 — /api/vad/status + /api/vad/pull-model endpoints.

In the test env onnxruntime is not installed, so this also pins the
runtime-absent behaviour: status reports it, and pull-model 503s cleanly
(the app keeps working — it falls back to silencedetect).
"""

from __future__ import annotations


def test_vad_status_reports_runtime_and_model(app_with_stub):
    r = app_with_stub.get("/api/vad/status")
    assert r.status_code == 200
    body = r.json()
    assert {"enabled", "runtime_present", "model_present", "available", "model_path"} <= body.keys()
    # onnxruntime absent in the test env → unavailable, falls back
    assert body["runtime_present"] is False
    assert body["model_present"] is False
    assert body["available"] is False


def test_vad_pull_model_503_without_runtime(app_with_stub):
    r = app_with_stub.post("/api/vad/pull-model")
    assert r.status_code == 503


def test_vad_config_toggle_persists_and_reflects(app_with_stub, tmp_path, monkeypatch):
    from subarr import config, config_store as cs

    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "ov.json"))
    monkeypatch.delenv("SUBARR_VAD_ENABLED", raising=False)
    prior = config.settings.vad_enabled
    try:
        r = app_with_stub.post("/api/vad/config", json={"enabled": False})
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is False
        assert body["env_controlled"] is False
        assert cs.load_overrides().get("vad_enabled") is False  # persisted
    finally:
        object.__setattr__(config.settings, "vad_enabled", prior)
