"""#529: `PLEX_URL` defaulted to `http://192.168.1.105:32400`, a private LAN
address from the development machine, baked into the product. Every other
optional media-server setting defaults to empty. Found during the README
audit (#528): Settings and the onboarding prefill showed that address to every
fresh install as if it were a suggestion.

An unset PLEX_URL must mean "not configured", exactly as JELLYFIN_URL does."""

from __future__ import annotations

import importlib


def _reload_config(monkeypatch, **env):
    for name in ("PLEX_URL", "PLEX_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from subarr import config

    importlib.reload(config)
    return config


def test_unset_plex_url_is_empty_not_a_lan_address(monkeypatch):
    config = _reload_config(monkeypatch)
    assert config.settings.plex_url == ""
    assert "192.168" not in config.settings.plex_url


def test_an_explicitly_empty_plex_url_stays_empty(monkeypatch):
    # _env_or maps "" to the default; an explicit PLEX_URL= must not resurrect
    # any default, because there is no sensible default for a LAN address.
    config = _reload_config(monkeypatch, PLEX_URL="")
    assert config.settings.plex_url == ""


def test_a_set_plex_url_is_honoured(monkeypatch):
    config = _reload_config(monkeypatch, PLEX_URL="http://plex.lan:32400")
    assert config.settings.plex_url == "http://plex.lan:32400"


def test_plex_client_built_from_an_unset_url_reports_not_configured(monkeypatch):
    config = _reload_config(monkeypatch)
    from subarr.integrations.plex import PlexClient

    client = PlexClient(
        base_url=config.settings.plex_url,
        token=config.settings.plex_token,
        default_section=config.settings.plex_section,
        path_prefix=config.settings.plex_path_prefix,
        media_root=str(config.settings.media_root),
    )
    assert client.is_configured() is False


def test_no_private_address_is_baked_into_config_source():
    import inspect

    from subarr import config

    assert "192.168." not in inspect.getsource(config)
