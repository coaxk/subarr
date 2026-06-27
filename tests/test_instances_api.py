"""#161 Phase 4A — instance + topology config API.

Endpoint tests run on the hermetic `app_with_stub` TestClient (subgen/docker/
integrations/ollama stubbed in lifespan). Instances are seeded via the real
POST path rather than a config reload, so the already-imported routers keep a
valid `settings` binding. The override store is redirected to a tmp file
(read at call-time by config_store.store_path()).
"""

import pytest


@pytest.fixture
def api(app_with_stub, monkeypatch, tmp_path):
    # Redirect the persisted override store to a temp file. store_path() reads
    # this env on every call, so it takes effect for all add/edit/remove writes
    # even though lifespan already ran (with an empty default store = clean baseline).
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "overrides.json"))
    return app_with_stub


@pytest.fixture
def no_network_rebuild(monkeypatch):
    """Replace the runtime client rebuild with a bundle-only swap (no subgen/
    ollama reprobe, no network) so add/edit/remove tests stay hermetic."""
    import subarr.routers.onboarding as onb

    async def _rebuild(state, reprobe=True):
        from subarr.coverage_engine import IntegrationBundle

        state.integrations = IntegrationBundle()

    monkeypatch.setattr(onb, "_rebuild_runtime_clients", _rebuild)


# ── Task 1: GET /api/instances ──────────────────────────────────────────────
def test_list_instances_returns_instance0_per_service(api):
    r = api.get("/api/instances")
    assert r.status_code == 200
    data = r.json()
    by_service = {
        svc: [i for i in data["instances"] if i["service"] == svc] for svc in ("sonarr", "radarr", "bazarr")
    }
    for svc in ("sonarr", "radarr", "bazarr"):
        defaults = [i for i in by_service[svc] if i["id"] == ""]
        assert len(defaults) == 1, svc
        assert defaults[0]["is_default"] is True
        assert "api_key" not in defaults[0]
        assert "has_api_key" in defaults[0]


# ── Task 2: POST /api/instances/test ────────────────────────────────────────
def test_test_connection_ok_for_sonarr(api, monkeypatch):
    import subarr.routers.instances as mod

    async def fake_probe(service, url, api_key):
        assert service == "sonarr"
        assert url == "http://sonarr2.test"
        return {"ok": True, "detail": "connected", "root_folders": ["/data/anime"]}

    monkeypatch.setattr(mod, "_probe_connection", fake_probe)
    r = api.post(
        "/api/instances/test",
        json={"service": "sonarr", "url": "http://sonarr2.test", "api_key": "k"},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "detail": "connected", "root_folders": ["/data/anime"]}


def test_test_connection_rejects_unknown_service(api):
    r = api.post("/api/instances/test", json={"service": "plex", "url": "x", "api_key": "k"})
    assert r.status_code == 422
