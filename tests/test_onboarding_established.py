"""#262: established-install detection + settings pre-fill for the wizard."""

from __future__ import annotations

from types import SimpleNamespace

from subarr.onboarding import apply_prefill, install_is_configured, settings_prefill


def _settings(**over):
    base = dict(
        media_root="/media/library",
        arr_path_prefix="/data/Media/",
        bazarr_url="http://bazarr:6767",
        bazarr_api_key="",
        sonarr_url="http://sonarr:8989",
        sonarr_api_key="",
        radarr_url="http://radarr:7878",
        radarr_api_key="",
        tautulli_url="http://tautulli:8181",
        tautulli_api_key="",
        subgen_url="http://subgen:9000",
        ollama_url="http://ollama:11434",
        ollama_model="qwen2.5:7b",
        plex_url="http://plex:32400",
        plex_token="",
    )
    base.update(over)
    return SimpleNamespace(**base)


# ─── install_is_configured ──────────────────────────────────────────


def test_blank_install_is_not_configured():
    assert install_is_configured(_settings()) is False


def test_bazarr_key_marks_configured():
    assert install_is_configured(_settings(bazarr_api_key="abc123")) is True


def test_any_arr_key_marks_configured():
    assert install_is_configured(_settings(sonarr_api_key="s")) is True
    assert install_is_configured(_settings(radarr_api_key="r")) is True
    assert install_is_configured(_settings(tautulli_api_key="t")) is True


def test_plex_token_marks_configured():
    assert install_is_configured(_settings(plex_token="tok")) is True


# ─── settings_prefill ───────────────────────────────────────────────


def test_prefill_includes_nonempty_fields():
    p = settings_prefill(_settings(bazarr_api_key="realkey"))
    assert p["media_root"] == "/media/library"
    assert p["bazarr_url"] == "http://bazarr:6767"
    assert p["bazarr_api_key"] == "realkey"


def test_prefill_omits_empty_secret_fields():
    p = settings_prefill(_settings())  # all api keys blank
    assert "bazarr_api_key" not in p
    assert "sonarr_api_key" not in p
    assert "plex_token" not in p
    # URLs + media_root still present (they have values)
    assert "media_root" in p
    assert "sonarr_url" in p


def test_prefill_media_root_is_string():
    # media_root may arrive as a Path on the real Settings; prefill must stringify.
    from pathlib import PurePosixPath

    # PurePosixPath keeps forward slashes on any host OS (the real Settings holds
    # a PosixPath in the Linux container; str() it regardless).
    p = settings_prefill(_settings(media_root=PurePosixPath("/mnt/media")))
    assert p["media_root"] == "/mnt/media"
    assert isinstance(p["media_root"], str)


# ─── apply_prefill (state merge) ────────────────────────────────────


def test_apply_prefill_fills_missing_fields():
    state = {"step": 0, "progress": {}}
    out = apply_prefill(state, _settings(sonarr_api_key="realkey"))
    # URL pre-filled from settings
    assert out["progress"]["sonarr_url"] == "http://sonarr:8989"
    # secret pre-filled but MASKED (never raw to the client)
    assert out["progress"]["sonarr_api_key"] == "••••lkey"


def test_apply_prefill_stored_value_wins():
    # The user typed a custom URL into the wizard — must not be overwritten.
    state = {"step": 2, "progress": {"sonarr_url": "http://custom:8989"}}
    out = apply_prefill(state, _settings(sonarr_url="http://sonarr:8989"))
    assert out["progress"]["sonarr_url"] == "http://custom:8989"


def test_apply_prefill_does_not_mutate_input():
    state = {"step": 0, "progress": {}}
    apply_prefill(state, _settings(bazarr_api_key="x"))
    assert state["progress"] == {}


# ─── masked-credential fallback for Test-connection ─────────────────


def test_masked_arr_key_falls_back_to_settings():
    from subarr.routers.onboarding import TestRequest, _resolve_masked_credential

    body = TestRequest(url="http://sonarr:8989", api_key="••••lkey")
    _resolve_masked_credential("sonarr", body, _settings(sonarr_api_key="realkey"))
    assert body.api_key == "realkey"


def test_blank_arr_key_falls_back_to_settings():
    from subarr.routers.onboarding import TestRequest, _resolve_masked_credential

    body = TestRequest(url="http://radarr:7878", api_key="")
    _resolve_masked_credential("radarr", body, _settings(radarr_api_key="rk"))
    assert body.api_key == "rk"


def test_real_typed_key_is_left_untouched():
    from subarr.routers.onboarding import TestRequest, _resolve_masked_credential

    body = TestRequest(url="http://sonarr:8989", api_key="user-typed-new")
    _resolve_masked_credential("sonarr", body, _settings(sonarr_api_key="realkey"))
    assert body.api_key == "user-typed-new"


def test_masked_plex_token_falls_back_to_settings():
    from subarr.routers.onboarding import TestRequest, _resolve_masked_credential

    body = TestRequest(url="http://plex:32400", token="••••oken")
    _resolve_masked_credential("plex", body, _settings(plex_token="realtoken"))
    assert body.token == "realtoken"


# ─── / redirect gate (the actual bug) ───────────────────────────────


def test_root_redirects_unconfigured_install_to_onboarding(app_with_stub, monkeypatch):
    import subarr.app as app_mod

    monkeypatch.setattr(app_mod, "install_is_configured", lambda s: False)
    app_with_stub.app.state.onboarding.reset()  # ensure not-complete
    r = app_with_stub.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/onboarding"


def test_root_redirects_configured_install_to_home(app_with_stub, monkeypatch):
    # #262: a configured-but-incomplete install must NOT be forced to the wizard.
    import subarr.app as app_mod

    monkeypatch.setattr(app_mod, "install_is_configured", lambda s: True)
    app_with_stub.app.state.onboarding.reset()  # not-complete, yet configured
    r = app_with_stub.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/home"
