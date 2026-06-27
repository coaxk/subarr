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


def test_test_connection_malformed_url_returns_ok_false_not_500(api):
    # A control char makes httpx.InvalidURL fire at client construction (before
    # the request) — the test button must surface {ok:false}, not a raw 500.
    r = api.post(
        "/api/instances/test",
        json={"service": "sonarr", "url": "http://\x00bad", "api_key": "k"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is False


# ── Task 3: POST /api/instances (add) ───────────────────────────────────────
def test_add_instance_persists_and_goes_live(api, no_network_rebuild):
    r = api.post(
        "/api/instances",
        json={"service": "sonarr", "name": "Anime", "url": "http://sonarr-anime.test", "api_key": "k"},
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["service"] == "sonarr"
    assert created["id"] == "anime"  # slugified from name
    assert created["is_default"] is False
    assert "api_key" not in created

    from subarr import config_store

    extras = config_store.load_overrides().get("instances", {})
    assert any(i["url"] == "http://sonarr-anime.test" for i in extras.get("sonarr", []))

    listed = api.get("/api/instances").json()["instances"]
    assert any(i["id"] == "anime" and i["service"] == "sonarr" for i in listed)


def test_add_duplicate_slug_is_409(api, no_network_rebuild):
    body = {"service": "sonarr", "name": "Anime", "url": "http://a.test", "api_key": "k"}
    assert api.post("/api/instances", json=body).status_code == 201
    dup = api.post("/api/instances", json={**body, "url": "http://b.test"})
    assert dup.status_code == 409


def test_add_rejects_unknown_service(api):
    r = api.post("/api/instances", json={"service": "plex", "name": "X", "url": "u", "api_key": "k"})
    assert r.status_code == 422


# ── Task 4: PUT /api/instances/{service}/{id} (edit) ────────────────────────
def test_edit_instance_url_and_keep_masked_key(api, no_network_rebuild):
    api.post(
        "/api/instances",
        json={"service": "bazarr", "name": "Anime", "url": "http://a.test", "api_key": "orig"},
    )
    r = api.put("/api/instances/bazarr/anime", json={"name": "Anime", "url": "http://a2.test"})
    assert r.status_code == 200, r.text
    assert r.json()["url"] == "http://a2.test"

    from subarr import config_store

    saved = next(i for i in config_store.load_overrides()["instances"]["bazarr"] if i.get("slug") == "anime")
    assert saved["url"] == "http://a2.test"
    assert saved["api_key"] == "orig"  # preserved


def test_edit_unknown_instance_is_404(api):
    r = api.put("/api/instances/sonarr/nope", json={"name": "X", "url": "http://x.test", "api_key": "k"})
    assert r.status_code == 404


def test_edit_default_instance_is_400(api):
    r = api.put("/api/instances/sonarr/%20", json={"name": "X", "url": "http://x.test", "api_key": "k"})
    assert r.status_code in (400, 404)


# ── Task 5: DELETE /api/instances/{service}/{id} (remove) ───────────────────
def test_delete_instance_removes_and_applies(api, no_network_rebuild):
    api.post(
        "/api/instances",
        json={"service": "radarr", "name": "Anime", "url": "http://a.test", "api_key": "k"},
    )
    r = api.delete("/api/instances/radarr/anime")
    assert r.status_code == 200, r.text
    listed = api.get("/api/instances").json()["instances"]
    assert not any(i["id"] == "anime" and i["service"] == "radarr" for i in listed)


def test_delete_unknown_instance_is_404(api):
    r = api.delete("/api/instances/sonarr/nope")
    assert r.status_code == 404


# ── Task 6: GET /api/topology ───────────────────────────────────────────────
def test_topology_lists_libraries_with_bindings(api):
    r = api.get("/api/topology")
    assert r.status_code == 200
    data = r.json()
    assert "libraries" in data and isinstance(data["libraries"], list)
    lib0 = data["libraries"][0]
    assert {"slug", "name", "sonarr_id", "radarr_id", "bazarr_id", "plex_section", "plex_matched"}.issubset(
        lib0
    )
    assert "binding_warnings" in data


# ── Task 8: single-stack back-compat guard ──────────────────────────────────
def test_single_stack_topology_is_clean(api):
    insts = api.get("/api/instances").json()["instances"]
    assert all(i["is_default"] for i in insts)
    topo = api.get("/api/topology").json()
    assert topo["binding_warnings"] == []


# ── #378: per-instance live health (GET /api/instances/health) ──────────────
def test_instances_health_fans_out_one_entry_per_instance(api, no_network_rebuild, monkeypatch):
    import subarr.routers.instances as mod

    # Seed a second sonarr so there are >=2 instances to fan out over.
    api.post(
        "/api/instances",
        json={"service": "sonarr", "name": "Anime", "url": "http://sonarr-anime.test", "api_key": "k"},
    )

    probed: list[tuple[str, str]] = []

    async def fake_probe(service, url, api_key):
        probed.append((service, url))
        # Only the anime instance is "up"; everything else is reachable-but-down.
        return {"ok": url == "http://sonarr-anime.test", "detail": "probed", "root_folders": []}

    monkeypatch.setattr(mod, "_probe_connection", fake_probe)

    r = api.get("/api/instances/health")
    assert r.status_code == 200, r.text
    health = r.json()["health"]

    # One entry per configured instance, each carrying identity + live status.
    insts = api.get("/api/instances").json()["instances"]
    assert len(health) == len(insts)
    for h in health:
        assert {"id", "service", "name", "online", "configured"}.issubset(h)

    anime = next(h for h in health if h["service"] == "sonarr" and h["id"] == "anime")
    assert anime["configured"] is True
    assert anime["online"] is True
    # The anime instance's real url was actually probed.
    assert ("sonarr", "http://sonarr-anime.test") in probed

    # A configured-but-down instance is reachable=configured, online=False.
    default_sonarr = next(h for h in health if h["service"] == "sonarr" and h["id"] == "")
    assert default_sonarr["configured"] is True
    assert default_sonarr["online"] is False


@pytest.mark.asyncio
async def test_probe_instance_skips_network_for_unconfigured(monkeypatch):
    import subarr.routers.instances as mod
    from subarr.instances import Instance

    async def boom(*a, **k):  # _probe_connection must NOT be called for unconfigured
        raise AssertionError("network probe attempted for an unconfigured instance")

    monkeypatch.setattr(mod, "_probe_connection", boom)

    inst = Instance(id="anime", service="sonarr", name="Anime", url="", api_key="")
    rec = await mod._probe_instance(inst)
    assert rec["configured"] is False
    assert rec["online"] is False
    assert rec["id"] == "anime"
    assert rec["service"] == "sonarr"
