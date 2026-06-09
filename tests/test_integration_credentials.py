"""Tests for in-app integration credential editing (#75).

Covers the new credential endpoints that let users add/change an
integration (URL, API key, Plex token) without editing env + restarting:

  PUT  /api/integrations/{name}/credentials  — validate + persist + live-rebuild
  POST /api/integrations/{name}/test         — test-connection-before-save

Hard rules pinned here:
  - env-set fields are AUTHORITATIVE and must never be overwritten by a UI
    write (mirrors the onboarding clobber guard).
  - a successful save persists to the config-store override file (survives
    restart) AND mutates the running Settings singleton (live, no restart).
  - saving rebuilds the affected client on app.state (reuses
    _rebuild_runtime_clients) so the new creds take effect immediately.
  - invalid input (unknown service, empty/garbage body) is rejected.
"""

from __future__ import annotations


# ─── config layer: credential fields are persistable + guardable ────


def test_credential_fields_are_in_field_env_vars(subarr_env):
    """Every editable credential field must be in FIELD_ENV_VARS so
    env_is_set() can protect an operator's env declaration."""
    from subarr import config

    for field in (
        "bazarr_url",
        "bazarr_api_key",
        "sonarr_url",
        "sonarr_api_key",
        "radarr_url",
        "radarr_api_key",
        "tautulli_url",
        "tautulli_api_key",
        "plex_url",
        "plex_token",
        "ollama_url",
        "ollama_model",
    ):
        assert field in config.FIELD_ENV_VARS, field


def test_credential_fields_are_persistable(subarr_env):
    """The config-store override layer must know how to coerce each
    credential field, else a saved value silently never re-applies on
    restart."""
    from subarr import config

    for field in (
        "bazarr_url",
        "bazarr_api_key",
        "sonarr_url",
        "sonarr_api_key",
        "radarr_url",
        "radarr_api_key",
        "tautulli_url",
        "tautulli_api_key",
        "plex_url",
        "plex_token",
        "ollama_url",
    ):
        assert field in config._FIELD_COERCE, field


def test_persisted_credential_reapplies_below_env(subarr_env, monkeypatch):
    """A saved override re-applies on the next config.load() — but only
    when the field is not env-set (env > file > default)."""
    from subarr import config, config_store

    monkeypatch.delenv("BAZARR_URL", raising=False)
    config_store.save_override("bazarr_url", "http://persisted:6767")
    s = config.load()
    assert s.bazarr_url == "http://persisted:6767"

    # With env set, the override must lose.
    monkeypatch.setenv("BAZARR_URL", "http://env-wins:6767")
    s2 = config.load()
    assert s2.bazarr_url == "http://env-wins:6767"


# ─── GET /api/integrations/{name}/config ────────────────────────────


def test_get_config_unknown_service_404(app_with_stub):
    r = app_with_stub.get("/api/integrations/nope/config")
    assert r.status_code == 404


def test_get_config_reports_env_managed_and_masks_secret(app_with_stub):
    """bazarr is env-set by the fixture → url + api_key both env_managed.
    The api_key value must be masked, never returned in the clear."""
    r = app_with_stub.get("/api/integrations/bazarr/config")
    assert r.status_code == 200
    fields = r.json()["fields"]
    assert fields["url"]["env_managed"] is True
    assert fields["api_key"]["env_managed"] is True
    assert fields["api_key"]["secret"] is True
    # masked: never the raw key
    assert "bz-test-key" not in fields["api_key"]["value"]
    assert fields["api_key"]["has_value"] is True
    # URL prefills verbatim
    assert fields["url"]["value"] == "http://bazarr.test:6767"


def test_get_config_reports_editable_when_not_env_set(app_with_stub, monkeypatch):
    monkeypatch.delenv("PLEX_TOKEN", raising=False)
    monkeypatch.delenv("PLEX_URL", raising=False)
    # force a fresh settings load reflecting the delenv
    from subarr import config

    monkeypatch.setattr(config, "settings", config.load())
    r = app_with_stub.get("/api/integrations/plex/config")
    assert r.status_code == 200
    fields = r.json()["fields"]
    assert fields["token"]["env_managed"] is False
    assert fields["url"]["env_managed"] is False


# ─── PUT /api/integrations/{name}/credentials ───────────────────────


def test_save_unknown_service_404(app_with_stub):
    r = app_with_stub.put(
        "/api/integrations/nope/credentials",
        json={"url": "http://x", "api_key": "k"},
    )
    assert r.status_code == 404


def test_save_empty_body_rejected(app_with_stub, monkeypatch):
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    r = app_with_stub.put("/api/integrations/ollama/credentials", json={})
    assert r.status_code == 400


def test_save_persists_and_applies_when_not_env_set(app_with_stub, monkeypatch):
    """Save a not-env-set field → it lands in the running Settings AND in
    the override store."""
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    from subarr import config_store
    from subarr.config import settings

    r = app_with_stub.put(
        "/api/integrations/ollama/credentials",
        json={"url": "http://new-ollama:11434", "model": "llama3"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True

    assert settings.ollama_url == "http://new-ollama:11434"
    assert settings.ollama_model == "llama3"
    overrides = config_store.load_overrides()
    assert overrides["ollama_url"] == "http://new-ollama:11434"
    assert overrides["ollama_model"] == "llama3"


def test_save_does_not_clobber_env_set_field(app_with_stub):
    """bazarr_url is env-set by the subarr_env fixture → the save must NOT
    overwrite it, and must report it as managed-by-env."""
    from subarr import config_store
    from subarr.config import settings

    before = settings.bazarr_url
    r = app_with_stub.put(
        "/api/integrations/bazarr/credentials",
        json={"url": "http://ui-attempt:6767", "api_key": "ui-key"},
    )
    assert r.status_code == 200
    body = r.json()
    # The env-set url must be untouched + flagged.
    assert settings.bazarr_url == before
    assert "bazarr_url" in body["managed_by_env"]
    # And the override store must NOT contain the rejected env-set field.
    assert "bazarr_url" not in config_store.load_overrides()


def test_save_rebuilds_client_on_app_state(app_with_stub, monkeypatch):
    """After a save, app.state.integrations must be a fresh instance so the
    new creds take effect live (no restart)."""
    monkeypatch.delenv("RADARR_URL", raising=False)
    app = app_with_stub.app
    old_bundle = app.state.integrations
    r = app_with_stub.put(
        "/api/integrations/radarr/credentials",
        json={"url": "http://new-radarr:7878", "api_key": "k"},
    )
    assert r.status_code == 200
    assert app.state.integrations is not old_bundle


def test_save_plex_token(app_with_stub, monkeypatch):
    """Plex uses `token`, not `api_key`. Saving it when not env-set lands
    in Settings + override store + rebuilds the plex client."""
    monkeypatch.delenv("PLEX_TOKEN", raising=False)
    monkeypatch.delenv("PLEX_URL", raising=False)
    from subarr import config_store
    from subarr.config import settings

    r = app_with_stub.put(
        "/api/integrations/plex/credentials",
        json={"url": "http://new-plex:32400", "token": "plex-tok"},
    )
    assert r.status_code == 200
    assert settings.plex_url == "http://new-plex:32400"
    assert settings.plex_token == "plex-tok"
    assert config_store.load_overrides()["plex_token"] == "plex-tok"


# ─── POST /api/integrations/{name}/test ─────────────────────────────


def test_test_connection_unknown_service_404(app_with_stub):
    r = app_with_stub.post("/api/integrations/nope/test", json={"url": "http://x", "api_key": "k"})
    assert r.status_code == 404


def test_test_connection_delegates_to_onboarding_probe(app_with_stub, monkeypatch):
    """The editor's /test must route to the SAME per-service probe the
    onboarding wizard uses (single source of reachability truth). Patch the
    sonarr probe and assert our endpoint surfaces its result verbatim, with
    the url + api_key forwarded."""
    import subarr.routers.onboarding as ob

    seen = {}

    async def fake_sonarr(body):
        seen["url"] = body.url
        seen["api_key"] = body.api_key
        return {"ok": True, "version": "4.0.1", "detail": "Sonarr 4.0.1", "error": None}

    monkeypatch.setattr(ob, "_test_sonarr", fake_sonarr)

    r = app_with_stub.post(
        "/api/integrations/sonarr/test",
        json={"url": "http://sonarr.test:8989", "api_key": "sn-test-key"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["version"] == "4.0.1"
    assert seen == {"url": "http://sonarr.test:8989", "api_key": "sn-test-key"}


def test_test_connection_unreachable(app_with_stub, monkeypatch):
    """An unreachable upstream returns ok=False with an error string, never
    a 500 — the probe handler raises and test_connection traps it."""
    import subarr.routers.onboarding as ob

    async def boom(body):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(ob, "_test_radarr", boom)

    r = app_with_stub.post(
        "/api/integrations/radarr/test",
        json={"url": "http://nope:7878", "api_key": "k"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"]
