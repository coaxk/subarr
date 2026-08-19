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


def test_concurrent_saves_do_not_lose_keys(tmp_path, monkeypatch):
    # The data-loss bug: unlocked read-modify-write lets two concurrent saves each
    # read the file and write back a dict missing the other's key.
    import threading

    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "ov.json"))
    from subarr import config_store as cs

    n = 40
    barrier = threading.Barrier(n)

    def worker(i):
        barrier.wait()  # maximize contention
        cs.save_override(f"k{i}", i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert cs.load_overrides() == {f"k{i}": i for i in range(n)}  # nothing lost


def test_save_does_not_clobber_present_but_unreadable_file(tmp_path, monkeypatch):
    import pytest

    p = tmp_path / "ov.json"
    p.write_text('{"sonarr_api_key": "keep-me", "bazarr_url": "http://b"}', encoding="utf-8")
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(p))
    from subarr import config_store as cs

    real_read = cs.Path.read_text

    def boom(self, *a, **k):
        if self.name == "ov.json":
            raise OSError("stale NFS handle")  # transient read failure
        return real_read(self, *a, **k)

    monkeypatch.setattr(cs.Path, "read_text", boom)
    with pytest.raises(cs.ConfigStoreError):
        cs.save_override("subgen_url", "http://s")  # must abort, not overwrite
    monkeypatch.setattr(cs.Path, "read_text", real_read)  # restore reads, keep the env override
    # the existing config survived untouched
    assert cs.load_overrides() == {"sonarr_api_key": "keep-me", "bazarr_url": "http://b"}


def test_corrupt_file_is_preserved_not_discarded(tmp_path, monkeypatch):
    p = tmp_path / "ov.json"
    p.write_text("{ garbage not json", encoding="utf-8")
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(p))
    from subarr import config_store as cs

    assert cs.load_overrides() == {}  # still fail-soft
    backups = list(tmp_path.glob("ov.json.corrupt-*"))
    assert len(backups) == 1 and "garbage" in backups[0].read_text("utf-8")


def test_write_is_fsynced(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "ov.json"))
    from subarr import config_store as cs

    calls = []
    monkeypatch.setattr(cs.os, "fsync", lambda fd: calls.append(fd))
    cs.save_override("k", 1)
    assert calls  # fsynced before the atomic replace


def test_save_overrides_merges_all_keys_in_one_write(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "ov.json"))
    from subarr import config_store as cs

    cs.save_override("existing", "kept")
    writes = []
    real_write = cs._write
    monkeypatch.setattr(cs, "_write", lambda data: (writes.append(data), real_write(data))[1])

    cs.save_overrides({"a": 1, "b": 2, "c": 3})  # N keys, one write

    assert len(writes) == 1  # ONE atomic write for N keys
    got = cs.load_overrides()
    assert got["existing"] == "kept"  # merges into existing keys, not replace
    assert got["a"] == 1 and got["b"] == 2 and got["c"] == 3


def test_clear_overrides_removes_only_listed_keys_in_one_write(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "ov.json"))
    from subarr import config_store as cs

    cs.save_overrides({"a": 1, "b": 2, "c": 3, "d": 4})
    writes = []
    real_write = cs._write
    monkeypatch.setattr(cs, "_write", lambda data: (writes.append(data), real_write(data))[1])

    cs.clear_overrides(["a", "c", "missing"])  # missing key is a no-op

    assert len(writes) == 1  # ONE atomic write for N keys
    assert cs.load_overrides() == {"b": 2, "d": 4}  # only the listed keys removed


def test_clear_overrides_no_write_when_no_keys_present(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "ov.json"))
    from subarr import config_store as cs

    cs.save_overrides({"a": 1})
    writes = []
    monkeypatch.setattr(cs, "_write", lambda data: writes.append(data))
    cs.clear_overrides(["missing", "nope"])  # nothing present -> no-op
    assert writes == []


def test_save_overrides_aborts_on_present_but_unreadable_file(tmp_path, monkeypatch):
    import pytest

    p = tmp_path / "ov.json"
    p.write_text('{"sonarr_api_key": "keep-me"}', encoding="utf-8")
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(p))
    from subarr import config_store as cs

    real_read = cs.Path.read_text

    def boom(self, *a, **k):
        if self.name == "ov.json":
            raise OSError("stale NFS handle")  # transient read failure
        return real_read(self, *a, **k)

    monkeypatch.setattr(cs.Path, "read_text", boom)
    with pytest.raises(cs.ConfigStoreError):
        cs.save_overrides({"a": 1, "b": 2})  # must abort, not overwrite
    monkeypatch.setattr(cs.Path, "read_text", real_read)
    # the existing config survived untouched
    assert cs.load_overrides() == {"sonarr_api_key": "keep-me"}


def test_bulk_write_is_fsynced(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "ov.json"))
    from subarr import config_store as cs

    calls = []
    monkeypatch.setattr(cs.os, "fsync", lambda fd: calls.append(fd))
    cs.save_overrides({"a": 1, "b": 2})
    assert calls  # fsynced before the atomic replace


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


def test_wizard_persists_credentials_across_restart(subarr_env, monkeypatch, tmp_path):
    # The config-loss bug (field report): the setup wizard applied credentials to
    # the running Settings so they worked live, but never persisted them, so they
    # vanished on the next restart. The wizard must write them to the store too.
    import importlib

    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "ov.json"))
    for v in ("SONARR_URL", "SONARR_API_KEY", "BAZARR_URL", "BAZARR_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    from subarr import config
    from subarr import config_store as cs

    importlib.reload(config)
    from subarr.routers.onboarding import _apply_progress_to_settings

    _apply_progress_to_settings(
        {
            "sonarr_url": "http://newsonarr:8989",
            "sonarr_api_key": "sk",
            "bazarr_url": "http://newbazarr:6767",
            "bazarr_api_key": "bk",
        }
    )
    ov = cs.load_overrides()
    assert ov.get("sonarr_url") == "http://newsonarr:8989"
    assert ov.get("sonarr_api_key") == "sk"
    assert ov.get("bazarr_url") == "http://newbazarr:6767"
    assert ov.get("bazarr_api_key") == "bk"


def test_wizard_persist_failure_does_not_undo_live_apply(subarr_env, monkeypatch, tmp_path):
    # Regression: persist is best-effort and SEPARATE from the live apply — a
    # save_override failure must never prevent the wizard's in-memory apply or
    # the instance/library rebuild.
    import importlib

    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "ov.json"))
    monkeypatch.delenv("SONARR_URL", raising=False)
    monkeypatch.delenv("SONARR_API_KEY", raising=False)
    from subarr import config
    from subarr import config_store as cs

    importlib.reload(config)
    monkeypatch.setattr(cs, "save_override", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    from subarr.routers.onboarding import _apply_progress_to_settings

    _apply_progress_to_settings({"sonarr_url": "http://s:8989", "sonarr_api_key": "k"})
    assert config.settings.sonarr_url == "http://s:8989"  # live apply survived
    inst0 = next(i for i in config.settings.instances if i.service == "sonarr" and i.id == "")
    assert inst0.url == "http://s:8989"  # rebuild ran despite the persist failure
