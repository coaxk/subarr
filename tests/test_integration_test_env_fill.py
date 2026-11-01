"""#649: Settings "Test connection" on an env-managed card.

The editor's buildBody() skips every env_managed field, so a card whose
fields are all set by env posts `{}` (and a mixed card posts only the
UI-owned fields). The /test route must fill each missing field from the
live settings instead of rejecting the request 422, so the probe tests what
is actually configured. An explicitly supplied value still wins
(test-before-save).
"""

from __future__ import annotations

import pytest


def _reload_settings(monkeypatch):
    from subarr import config

    monkeypatch.setattr(config, "settings", config.load())


def _capture(monkeypatch, probe_name):
    import subarr.routers.onboarding as ob

    seen: dict = {}

    async def fake(body):
        seen.update(body.model_dump())
        return {"ok": True, "version": "1.0", "detail": "fake", "error": None}

    monkeypatch.setattr(ob, probe_name, fake)
    return seen


def test_all_env_managed_empty_body_tests_configured_values(app_with_stub, monkeypatch):
    """The issue's repro: SONARR_URL + SONARR_API_KEY in env, body {}."""
    seen = _capture(monkeypatch, "_test_sonarr")
    r = app_with_stub.post("/api/integrations/sonarr/test", json={})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert seen["url"] == "http://sonarr.test:8989"
    assert seen["api_key"] == "sn-test-key"


def test_mixed_card_fills_only_the_missing_field(app_with_stub, monkeypatch):
    """URL env-managed (absent from body), key typed in the UI: the typed key
    is tested, the URL comes from settings."""
    seen = _capture(monkeypatch, "_test_sonarr")
    r = app_with_stub.post("/api/integrations/sonarr/test", json={"api_key": "typed-new-key"})
    assert r.status_code == 200, r.text
    assert seen["url"] == "http://sonarr.test:8989"
    assert seen["api_key"] == "typed-new-key"


def test_supplied_url_still_wins(app_with_stub, monkeypatch):
    seen = _capture(monkeypatch, "_test_sonarr")
    r = app_with_stub.post("/api/integrations/sonarr/test", json={"url": "http://edited:1"})
    assert r.status_code == 200, r.text
    assert seen["url"] == "http://edited:1"
    assert seen["api_key"] == "sn-test-key"


def test_blank_url_is_treated_as_missing(app_with_stub, monkeypatch):
    seen = _capture(monkeypatch, "_test_sonarr")
    r = app_with_stub.post("/api/integrations/sonarr/test", json={"url": ""})
    assert r.status_code == 200, r.text
    assert seen["url"] == "http://sonarr.test:8989"


def test_nothing_configured_is_a_clear_failure_not_a_422(app_with_stub, monkeypatch):
    # jellyfin has no default URL (radarr defaults to http://radarr:7878).
    monkeypatch.delenv("JELLYFIN_URL", raising=False)
    monkeypatch.delenv("JELLYFIN_API_KEY", raising=False)
    _reload_settings(monkeypatch)
    seen = _capture(monkeypatch, "_test_jellyfin")
    r = app_with_stub.post("/api/integrations/jellyfin/test", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert "url" in body["error"].lower()
    assert seen == {}, "the probe must not run without a URL"


def test_jellyfin_env_api_key_is_filled(app_with_stub, monkeypatch):
    """Jellyfin had no stored-secret fallback at all, so even a masked or
    blank key reached the probe empty."""
    monkeypatch.setenv("JELLYFIN_URL", "http://jellyfin.test:8096")
    monkeypatch.setenv("JELLYFIN_API_KEY", "jf-env-key")
    _reload_settings(monkeypatch)
    seen = _capture(monkeypatch, "_test_jellyfin")
    r = app_with_stub.post("/api/integrations/jellyfin/test", json={})
    assert r.status_code == 200, r.text
    assert seen["url"] == "http://jellyfin.test:8096"
    assert seen["api_key"] == "jf-env-key"


def test_jellyfin_masked_key_is_replaced(app_with_stub, monkeypatch):
    monkeypatch.setenv("JELLYFIN_URL", "http://jellyfin.test:8096")
    monkeypatch.setenv("JELLYFIN_API_KEY", "jf-env-key")
    _reload_settings(monkeypatch)
    seen = _capture(monkeypatch, "_test_jellyfin")
    r = app_with_stub.post("/api/integrations/jellyfin/test", json={"api_key": "••••-key"})
    assert r.status_code == 200, r.text
    assert seen["api_key"] == "jf-env-key"


def test_plex_env_url_and_token_are_filled(app_with_stub, monkeypatch):
    monkeypatch.setenv("PLEX_URL", "http://plex.test:32400")
    monkeypatch.setenv("PLEX_TOKEN", "px-env-token")
    _reload_settings(monkeypatch)
    seen = _capture(monkeypatch, "_test_plex")
    r = app_with_stub.post("/api/integrations/plex/test", json={})
    assert r.status_code == 200, r.text
    assert seen["url"] == "http://plex.test:32400"
    assert seen["token"] == "px-env-token"


def test_openai_env_fields_are_filled(app_with_stub, monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://llm.test:8000/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "oa-env-key")
    monkeypatch.setenv("OPENAI_MODEL", "env-model")
    _reload_settings(monkeypatch)
    seen = _capture(monkeypatch, "_test_openai")
    r = app_with_stub.post("/api/integrations/openai/test", json={})
    assert r.status_code == 200, r.text
    assert seen["url"] == "http://llm.test:8000/v1"
    assert seen["api_key"] == "oa-env-key"
    assert seen["model"] == "env-model"


@pytest.mark.parametrize(
    ("svc", "probe"),
    [("ollama", "_test_ollama"), ("subgen", "_test_subgen")],
)
def test_url_only_services_fill_url(app_with_stub, monkeypatch, svc, probe):
    seen = _capture(monkeypatch, probe)
    r = app_with_stub.post(f"/api/integrations/{svc}/test", json={})
    assert r.status_code == 200, r.text
    assert seen["url"], f"{svc} url was not filled from settings"
