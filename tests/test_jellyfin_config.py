import importlib


def test_jellyfin_config_loads_from_env(monkeypatch):
    monkeypatch.setenv("JELLYFIN_URL", "http://jf:8096")
    monkeypatch.setenv("JELLYFIN_API_KEY", "secret")
    monkeypatch.setenv("JELLYFIN_PATH_PREFIX", "/media")
    from subarr import config

    importlib.reload(config)
    s = config.load()
    assert s.jellyfin_url == "http://jf:8096"
    assert s.jellyfin_api_key == "secret"
    assert s.jellyfin_path_prefix == "/media"


def test_jellyfin_defaults_empty(monkeypatch):
    for v in ("JELLYFIN_URL", "JELLYFIN_API_KEY", "JELLYFIN_PATH_PREFIX"):
        monkeypatch.delenv(v, raising=False)
    from subarr import config

    importlib.reload(config)
    s = config.load()
    assert s.jellyfin_url == "" and s.jellyfin_api_key == "" and s.jellyfin_path_prefix == ""
