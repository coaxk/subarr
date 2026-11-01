"""#616 — settings for the OpenAI-compatible LLM provider.

Six Settings fields, all UI-persistable and env-guardable, with one set of
normalisation rules shared by the env path in `config.load()` and the
persisted-override path. Each test pins `SUBARR_CONFIG_STORE` at a tmp file
and clears the six env vars so the host environment cannot leak in.
"""

from __future__ import annotations

import logging

import pytest

# attribute -> env var, in the order of the design table.
_ENV_VARS = {
    "llm_provider": "LLM_PROVIDER",
    "openai_base_url": "OPENAI_BASE_URL",
    "openai_api_key": "OPENAI_API_KEY",
    "openai_model": "OPENAI_MODEL",
    "openai_vision": "OPENAI_VISION",
    "openai_server_type": "OPENAI_SERVER_TYPE",
}

_DEFAULTS = {
    "llm_provider": "ollama",
    "openai_base_url": "",
    "openai_api_key": "",
    "openai_model": "",
    "openai_vision": "off",
    "openai_server_type": "auto",
}


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """No real override file is read and none of the six env vars leak in."""
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "overrides.json"))
    for name in _ENV_VARS.values():
        monkeypatch.delenv(name, raising=False)
    return tmp_path


# ─── a. all unset ───────────────────────────────────────────────────


def test_all_unset_gives_documented_defaults():
    from subarr import config

    s = config.load()
    for attr, default in _DEFAULTS.items():
        assert getattr(s, attr) == default, attr


# ─── b. each env var set to a valid value ───────────────────────────


def test_each_env_var_populates_its_attribute(monkeypatch):
    from subarr import config

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://unsloth:8000/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-live")
    monkeypatch.setenv("OPENAI_MODEL", "Org/Model-X")
    monkeypatch.setenv("OPENAI_VISION", "same")
    monkeypatch.setenv("OPENAI_SERVER_TYPE", "openrouter")

    s = config.load()
    assert s.llm_provider == "openai"
    assert s.openai_base_url == "http://unsloth:8000/v1"
    assert s.openai_api_key == "sk-live"
    assert s.openai_model == "Org/Model-X"
    assert s.openai_vision == "same"
    assert s.openai_server_type == "openrouter"


# ─── c. whitespace stripped, trailing slash + case inside values kept ─


def test_whitespace_stripped_but_value_body_preserved(monkeypatch):
    from subarr import config

    monkeypatch.setenv("OPENAI_API_KEY", " sk-x\n")
    monkeypatch.setenv("OPENAI_BASE_URL", "  http://unsloth:8000/v1/ \n")
    monkeypatch.setenv("OPENAI_MODEL", " Org/Model-X ")

    s = config.load()
    assert s.openai_api_key == "sk-x"
    # Rule 5: no trailing-slash / /v1 munging here — that's the client's job.
    assert s.openai_base_url == "http://unsloth:8000/v1/"
    assert s.openai_model == "Org/Model-X"


# ─── d. empty / whitespace-only means the default ───────────────────


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_values_fall_back_to_defaults(monkeypatch, blank):
    from subarr import config

    for name in _ENV_VARS.values():
        monkeypatch.setenv(name, blank)

    s = config.load()
    for attr, default in _DEFAULTS.items():
        assert getattr(s, attr) == default, attr


# ─── e. case rules ──────────────────────────────────────────────────


def test_case_normalisation(monkeypatch):
    from subarr import config

    monkeypatch.setenv("LLM_PROVIDER", "OpenAI")
    monkeypatch.setenv("OPENAI_SERVER_TYPE", "OpenRouter")
    monkeypatch.setenv("OPENAI_VISION", "OFF")

    s = config.load()
    assert s.llm_provider == "openai"
    assert s.openai_server_type == "openrouter"
    assert s.openai_vision == "off"


def test_vision_same_is_case_insensitive(monkeypatch):
    from subarr import config

    monkeypatch.setenv("OPENAI_VISION", " Same ")

    assert config.load().openai_vision == "same"


def test_vision_model_id_keeps_its_case(monkeypatch):
    from subarr import config

    monkeypatch.setenv("OPENAI_VISION", "Qwen/Qwen2.5-VL-7B")

    assert config.load().openai_vision == "Qwen/Qwen2.5-VL-7B"


# ─── f. unrecognised values fall back with a warning, no raise ──────


def test_unknown_provider_falls_back_to_ollama(monkeypatch, caplog):
    from subarr import config

    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    with caplog.at_level(logging.WARNING):
        s = config.load()

    assert s.llm_provider == "ollama"
    assert any("anthropic" in r.getMessage() for r in caplog.records)


def test_unknown_server_type_falls_back_to_auto(monkeypatch, caplog):
    from subarr import config

    monkeypatch.setenv("OPENAI_SERVER_TYPE", "azure")
    with caplog.at_level(logging.WARNING):
        s = config.load()

    assert s.openai_server_type == "auto"
    assert any("azure" in r.getMessage() for r in caplog.records)


# ─── g. the key never reaches a log line ────────────────────────────


def test_api_key_never_logged(monkeypatch, caplog):
    from subarr import config

    key = "sk-616-distinctive-secret"
    monkeypatch.setenv("OPENAI_API_KEY", key)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")  # forces the warning path

    with caplog.at_level(logging.DEBUG):
        config.load()

    assert caplog.records  # the fallback warning was emitted ...
    assert all(key not in r.getMessage() for r in caplog.records)  # ... without the key


# ─── h. persisted UI values are normalised by the same rules ────────


def test_persisted_overrides_are_normalised(monkeypatch):
    from subarr import config, config_store

    config_store.save_override("llm_provider", "OpenAI")
    config_store.save_override("openai_base_url", "  http://unsloth:8000/v1/ \n")
    config_store.save_override("openai_api_key", " sk-y\n")
    config_store.save_override("openai_model", " Org/Model-X ")
    config_store.save_override("openai_vision", "SAME")
    config_store.save_override("openai_server_type", "OpenRouter")

    s = config.load()
    assert s.llm_provider == "openai"
    assert s.openai_base_url == "http://unsloth:8000/v1/"
    assert s.openai_api_key == "sk-y"
    assert s.openai_model == "Org/Model-X"
    assert s.openai_vision == "same"
    assert s.openai_server_type == "openrouter"


# ─── i. env wins over the persisted file ────────────────────────────


def test_env_wins_over_persisted(monkeypatch):
    from subarr import config, config_store

    config_store.save_override("openai_model", "file-model")
    monkeypatch.setenv("OPENAI_MODEL", "env-model")

    assert config.load().openai_model == "env-model"


# ─── j. env guarding + registration in both maps ────────────────────


def test_env_is_set_for_each_field(monkeypatch):
    from subarr import config

    for attr, name in _ENV_VARS.items():
        monkeypatch.setenv(name, "x")
        assert config.env_is_set(attr) is True, attr
        monkeypatch.delenv(name, raising=False)
        assert config.env_is_set(attr) is False, attr


def test_fields_registered_in_both_maps():
    from subarr import config

    for attr, name in _ENV_VARS.items():
        assert config.FIELD_ENV_VARS[attr] == name, attr
        assert attr in config._FIELD_COERCE, attr


# ─── k. existing ollama defaults unchanged ──────────────────────────


def test_ollama_defaults_unchanged():
    from subarr import config

    s = config.load()
    assert s.ollama_url == "http://ollama:11434"
    assert s.ollama_model == "qwen2.5:7b"
    assert s.ollama_vision_model == "qwen2.5vl:7b"
