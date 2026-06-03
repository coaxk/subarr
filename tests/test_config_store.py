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
    assert cs.load_overrides() == {}   # never raises on a garbage file


def test_file_override_applies_when_env_unset(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "ov.json"))
    monkeypatch.delenv("SUBARR_VAD_ENABLED", raising=False)
    from subarr import config, config_store as cs
    cs.save_override("vad_enabled", False)   # UI turned it off
    s = config.load()
    assert s.vad_enabled is False            # default would be True → file won


def test_env_beats_file_override(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "ov.json"))
    monkeypatch.setenv("SUBARR_VAD_ENABLED", "1")   # operator pinned ON
    from subarr import config, config_store as cs
    cs.save_override("vad_enabled", False)          # UI tried OFF
    s = config.load()
    assert s.vad_enabled is True                    # env is authoritative
