"""#112 — config persistence layer.

Precedence is the whole point: env (operator, authoritative) > persisted
file (UI/wizard) > built-in default. These tests pin both the store
round-trip and that precedence.
"""

from __future__ import annotations


def test_store_save_load_clear_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "ov.json"))
    from subarr import config_store as cs

    assert cs.load_overrides() == {}
    cs.save_override("vad_enabled", False)
    cs.save_override("ollama_model", "qwen")
    assert cs.load_overrides() == {"vad_enabled": False, "ollama_model": "qwen"}
    cs.clear_override("vad_enabled")
    got = cs.load_overrides()
    assert "vad_enabled" not in got and got["ollama_model"] == "qwen"


def test_corrupt_store_is_ignored(tmp_path, monkeypatch):
    p = tmp_path / "ov.json"
    p.write_text("{ not valid json", encoding="utf-8")
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(p))
    from subarr import config_store as cs

    assert cs.load_overrides() == {}  # never raises on a garbage file


def test_file_override_applies_when_env_unset(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "ov.json"))
    monkeypatch.delenv("SUBARR_VAD_ENABLED", raising=False)
    from subarr import config, config_store as cs

    cs.save_override("vad_enabled", False)  # UI turned it off
    s = config.load()
    assert s.vad_enabled is False  # default would be True → file won


def test_env_beats_file_override(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "ov.json"))
    monkeypatch.setenv("SUBARR_VAD_ENABLED", "1")  # operator pinned ON
    from subarr import config, config_store as cs

    cs.save_override("vad_enabled", False)  # UI tried OFF
    s = config.load()
    assert s.vad_enabled is True  # env is authoritative


def test_rebuild_instances_seeds_instance0_from_scalars(subarr_env):
    from subarr import config

    s = config.settings  # the reloaded singleton (subarr_env seeded it from env)
    sonarrs = [i for i in s.instances if i.service == "sonarr"]
    inst0 = next(i for i in sonarrs if i.id == "")
    assert inst0.url == s.sonarr_url  # "http://sonarr.test:8989" from subarr_env
    assert inst0.api_key == s.sonarr_api_key


def test_rebuild_instances_picks_up_extras(subarr_env, monkeypatch, tmp_path):
    import importlib
    import json

    store = tmp_path / "ov.json"
    store.write_text(
        json.dumps({"instances": {"sonarr": [{"name": "Anime", "url": "http://s2:8989", "api_key": "kk"}]}})
    )
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(store))
    from subarr import config

    importlib.reload(config)
    ids = {i.id for i in config.settings.instances if i.service == "sonarr"}
    assert "" in ids and "anime" in ids


def test_rebuild_instances_failsoft_on_bad_config(subarr_env, monkeypatch, tmp_path):
    import importlib
    import json

    store = tmp_path / "ov.json"
    store.write_text(
        json.dumps(
            {
                "instances": {"sonarr": [{"name": "x", "url": "u"}]}  # missing api_key
            }
        )
    )
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(store))
    from subarr import config

    importlib.reload(config)  # must not raise
    assert len([i for i in config.settings.instances if i.service == "sonarr"]) == 1


def test_credential_flush_rebuilds_instances(subarr_env, monkeypatch):
    # #161 back-compat: a wizard credential edit must refresh settings.instances
    # (instance 0), else the rebuilt IntegrationBundle would use a stale URL.
    # sonarr_url must NOT be env-set, or the clobber-guard skips the flush.
    import importlib

    monkeypatch.delenv("SONARR_URL", raising=False)
    monkeypatch.delenv("SONARR_API_KEY", raising=False)
    from subarr import config

    importlib.reload(config)
    from subarr.routers.onboarding import _apply_progress_to_settings

    _apply_progress_to_settings({"sonarr_url": "http://newsonarr:8989", "sonarr_api_key": "newkey"})
    inst0 = next(i for i in config.settings.instances if i.service == "sonarr" and i.id == "")
    assert inst0.url == "http://newsonarr:8989"
    assert inst0.api_key == "newkey"
