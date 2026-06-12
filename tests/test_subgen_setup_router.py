"""Guided setup API: detect cascade, plan with mode routing, capability-gated apply."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(subarr_env, monkeypatch):
    from subarr.app import app

    with TestClient(app) as c:
        yield c


def _caps(reachable=True, is_subarr=True, runtime=True):
    return type("C", (), {"reachable": reachable, "is_subarr_subgen": is_subarr, "runtime_config": runtime})()


def test_detect_reports_gpu_when_smi_available(client, monkeypatch):
    import subarr.routers.subgen_setup as ss

    async def fake_smi():
        return "NVIDIA GeForce RTX 3060, 12288, 4343, 8.6, 610.52"

    monkeypatch.setattr(ss, "_run_local_smi", fake_smi)
    r = client.get("/api/subgen-setup/detect")
    assert r.status_code == 200
    d = r.json()
    assert d["tier"] == "smi"
    assert d["gpu"]["name"].endswith("3060")
    assert d["gpu"]["compute_cap"] == 8.6
    assert d["recommended_model"] == "large-v3"


def test_detect_falls_to_docker_info_then_manual(client, monkeypatch):
    import subarr.routers.subgen_setup as ss

    async def no_smi():
        return None

    async def runtime_yes(app):
        return True

    monkeypatch.setattr(ss, "_run_local_smi", no_smi)
    monkeypatch.setattr(ss, "_nvidia_runtime", runtime_yes)
    r = client.get("/api/subgen-setup/detect")
    d = r.json()
    assert d["tier"] == "docker_info"
    assert d["gpu"] is None
    assert d["host_gpu_capable"] is True
    assert "nvidia-smi --query-gpu" in d["smi_command"]
    assert "utility" in d["passthrough_snippet"]


def test_parse_smi_endpoint(client):
    r = client.post("/api/subgen-setup/parse-smi", json={"pasted": "NVIDIA GeForce GTX 1070, 8192, 6.1"})
    d = r.json()
    assert d["ok"] is True
    assert d["gpu"]["vram_total_mb"] == 8192
    assert d["recommended_model"] == "medium"

    r2 = client.post("/api/subgen-setup/parse-smi", json={"pasted": "garbage"})
    assert r2.json()["ok"] is False


def test_plan_derives_compute_and_routes_live_apply(client, monkeypatch):
    import subarr.routers.subgen_setup as ss

    monkeypatch.setattr(ss, "_subgen_caps", lambda app: _caps(True, True, True))
    r = client.post(
        "/api/subgen-setup/plan", json={"model": "large-v3", "vram_total_mb": 12288, "compute_cap": 8.6}
    )
    d = r.json()
    assert d["compute_type"] == "float16"
    assert "half-precision" in d["compute_reason"]
    assert d["mode"] == "live_apply"
    assert "WHISPER_MODEL: large-v3" in d["env_additions"]  # A always available


def test_plan_mode_routing_matrix(client, monkeypatch):
    import subarr.routers.subgen_setup as ss

    # vanilla subgen -> generate + switch upsell
    monkeypatch.setattr(ss, "_subgen_caps", lambda app: _caps(True, False, False))
    d = client.post(
        "/api/subgen-setup/plan", json={"model": "medium", "vram_total_mb": 8192, "compute_cap": 8.6}
    ).json()
    assert (d["mode"], d["upsell"]) == ("generate", "switch_to_subarr_subgen")

    # old subarr-subgen -> generate + upgrade upsell
    monkeypatch.setattr(ss, "_subgen_caps", lambda app: _caps(True, True, False))
    d = client.post(
        "/api/subgen-setup/plan", json={"model": "medium", "vram_total_mb": 8192, "compute_cap": 8.6}
    ).json()
    assert (d["mode"], d["upsell"]) == ("generate", "upgrade_to_r9")

    # no subgen -> greenfield compose (and CPU matrix row)
    monkeypatch.setattr(ss, "_subgen_caps", lambda app: _caps(False, False, False))
    d = client.post(
        "/api/subgen-setup/plan", json={"model": "small", "vram_total_mb": None, "compute_cap": None}
    ).json()
    assert d["mode"] == "generate_full"
    assert "ghcr.io/coaxk/subarr-subgen" in d["compose"]
    assert d["compute_type"] == "int8"
    assert d["device"] == "cpu"


def test_apply_precheck_blocks_no_fit(client, monkeypatch):
    import subarr.routers.subgen_setup as ss

    monkeypatch.setattr(ss, "_subgen_caps", lambda app: _caps(True, True, True))
    d = client.post(
        "/api/subgen-setup/apply",
        json={"model": "large-v3", "compute_type": "float16", "vram_total_mb": 2048},
    ).json()
    assert d["ok"] is False
    assert d["reason"] == "precheck_no_fit"


def test_apply_not_supported_without_capability(client, monkeypatch):
    import subarr.routers.subgen_setup as ss

    monkeypatch.setattr(ss, "_subgen_caps", lambda app: _caps(True, True, False))
    d = client.post("/api/subgen-setup/apply", json={"model": "small", "compute_type": "int8"}).json()
    assert d["ok"] is False and d["reason"] == "not_supported"


def test_apply_surfaces_subgen_rollback(client, monkeypatch):
    import subarr.routers.subgen_setup as ss

    monkeypatch.setattr(ss, "_subgen_caps", lambda app: _caps(True, True, True))

    async def fake_post_config(app, **kw):
        return 422, {
            "ok": False,
            "reason": "oom",
            "rolled_back": True,
            "current_model": "medium",
            "current_compute_type": "float16",
        }

    monkeypatch.setattr(ss, "_post_config", fake_post_config)
    d = client.post(
        "/api/subgen-setup/apply",
        json={"model": "large-v3", "compute_type": "float16", "vram_total_mb": 12288},
    ).json()
    assert d["ok"] is False and d["reason"] == "oom"
    assert d["current_model"] == "medium"
    assert d["rolled_back"] is True


def test_apply_success(client, monkeypatch):
    import subarr.routers.subgen_setup as ss

    monkeypatch.setattr(ss, "_subgen_caps", lambda app: _caps(True, True, True))

    async def fake_post_config(app, **kw):
        return 200, {"ok": True, "model": "small", "compute_type": "float16"}

    monkeypatch.setattr(ss, "_post_config", fake_post_config)
    d = client.post(
        "/api/subgen-setup/apply", json={"model": "small", "compute_type": "float16", "vram_total_mb": 12288}
    ).json()
    assert d == {"ok": True, "model": "small", "compute_type": "float16"}
