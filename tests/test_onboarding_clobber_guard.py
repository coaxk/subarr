"""Onboarding clobber guard.

env-set integration config is the operator's authoritative declaration and
must NOT be overwritten by stored wizard progress on /complete — that's the
bug that broke subgen on dev (stale progress subgen_url clobbered the
env-set value). Fields NOT set via env still apply from the wizard (the
primary first-run path).

The `subarr_env` fixture sets every integration env var, so by default all
fields are env-set; tests delenv a field to exercise the apply path.
"""

from __future__ import annotations


def _apply():
    from subarr.routers.onboarding import _apply_progress_to_settings

    return _apply_progress_to_settings


def test_env_is_set_helper(subarr_env, monkeypatch):
    from subarr import config

    assert config.env_is_set("bazarr_url") is True  # set by subarr_env
    monkeypatch.delenv("BAZARR_URL", raising=False)
    assert config.env_is_set("bazarr_url") is False
    monkeypatch.setenv("BAZARR_URL", "   ")  # blank == unset
    assert config.env_is_set("bazarr_url") is False
    assert config.env_is_set("not_a_real_field") is False


def test_env_set_url_not_clobbered(subarr_env):
    from subarr.config import settings

    before = settings.bazarr_url
    _apply()({"bazarr_url": "http://stale-wizard:6767"})
    assert settings.bazarr_url == before
    assert settings.bazarr_url != "http://stale-wizard:6767"


def test_env_set_api_key_not_clobbered(subarr_env):
    from subarr.config import settings

    before = settings.bazarr_api_key
    _apply()({"bazarr_api_key": "stale-key"})
    assert settings.bazarr_api_key == before


def test_applies_when_field_not_env_set(subarr_env, monkeypatch):
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    from subarr.config import settings

    _apply()({"ollama_model": "wizard-chosen-model"})
    assert settings.ollama_model == "wizard-chosen-model"


def test_applies_when_env_blank(subarr_env, monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "   ")  # blank == unset → wizard applies
    from subarr.config import settings

    _apply()({"ollama_model": "wizard-model-2"})
    assert settings.ollama_model == "wizard-model-2"


def test_idempotent_for_env_set(subarr_env):
    from subarr.config import settings

    before = settings.subgen_url
    _apply()({"subgen_url": "http://stale-subgen:9000"})
    _apply()({"subgen_url": "http://stale-subgen:9000"})
    assert settings.subgen_url == before  # the actual dev-clobber scenario


def test_field_env_map_covers_all_apply_keys(subarr_env):
    import dataclasses
    from subarr import config
    from subarr.config import Settings

    apply_attrs = {
        "media_root",
        "arr_path_prefix",
        "bazarr_url",
        "bazarr_api_key",
        "sonarr_url",
        "sonarr_api_key",
        "radarr_url",
        "radarr_api_key",
        "tautulli_url",
        "tautulli_api_key",
        "subgen_url",
        "ollama_url",
        "ollama_model",
    }
    # every field the wizard can apply must be guardable
    assert apply_attrs <= set(config.FIELD_ENV_VARS)
    # and every mapped attr must be a real Settings field
    fields = {f.name for f in dataclasses.fields(Settings)}
    for attr in config.FIELD_ENV_VARS:
        assert attr in fields
