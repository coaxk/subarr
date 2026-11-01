"""Tests for the LLM provider kind reported in telemetry integrations (#616).

Edge cases:
  - test_ollama_provider_reports_neither
  - test_openai_local
  - test_openai_hosted
  - test_openai_incomplete_reports_neither
  - test_values_are_bools
  - test_other_integration_keys_unchanged
  - test_payload_has_no_llm_secrets
  - test_no_new_top_level_key
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest


@dataclass
class _FakeCaps:
    reachable: bool = True
    version: str | None = "2026.05.3"
    is_subarr_subgen: bool = True


_SETTINGS_FIELDS = (
    "llm_provider",
    "openai_base_url",
    "openai_api_key",
    "openai_model",
    "openai_server_type",
)


@pytest.fixture
def settings_store(subarr_env):
    from subarr import config

    originals = {name: getattr(config.settings, name) for name in _SETTINGS_FIELDS}
    yield config.settings
    for name, value in originals.items():
        object.__setattr__(config.settings, name, value)


@pytest.fixture
def db(tmp_path: Path, subarr_env) -> Path:
    from subarr.migrate import run_migrations

    p = tmp_path / "t.db"
    run_migrations(p)
    return p


def _set(settings, **values) -> None:
    for name, value in values.items():
        object.__setattr__(settings, name, value)


def _fake_app_state(db_path: Path, ollama_probe_result) -> SimpleNamespace:
    from subarr.error_store import ErrorStore
    from subarr.probe_store import ProbeStore
    from subarr.scan_store import ScanStore
    from subarr.schedule_store import ScheduleStore

    return SimpleNamespace(
        probe_store=ProbeStore(db_path),
        scans=ScanStore(db_path),
        errors=ErrorStore(db_path),
        schedule=ScheduleStore(db_path),
        ollama_probe_result=ollama_probe_result,
    )


def _provider_out(db_path: Path, ollama_probe_result=None) -> dict:
    from subarr.telemetry import make_default_stats_provider

    state = _fake_app_state(db_path, ollama_probe_result)
    return make_default_stats_provider(state)()


def _full_payload(db_path: Path) -> dict:
    from subarr.telemetry import TelemetryCollector, make_default_stats_provider

    state = _fake_app_state(db_path, None)
    collector = TelemetryCollector(
        db_path=db_path,
        endpoint="",
        subarr_version="v1.0.0",
        stats_provider=make_default_stats_provider(state),
        subgen_caps_provider=lambda: _FakeCaps(),
    )
    return collector.build_payload().to_dict()


def test_ollama_provider_reports_neither(settings_store, db: Path):
    _set(
        settings_store,
        llm_provider="ollama",
        openai_base_url="http://unsloth:8000/v1",
        openai_model="m",
    )
    out = _provider_out(db)
    assert out["integrations"]["openai_local"] is False
    assert out["integrations"]["openai_hosted"] is False


@pytest.mark.parametrize(
    ("base_url", "server_type"),
    [
        ("http://unsloth:8000/v1", "auto"),
        ("http://ollama:11434/v1", "auto"),
        ("https://api.openai.com/v1", "local"),
    ],
)
def test_openai_local(settings_store, db: Path, base_url: str, server_type: str):
    _set(
        settings_store,
        llm_provider="openai",
        openai_base_url=base_url,
        openai_model="m",
        openai_server_type=server_type,
    )
    out = _provider_out(db)
    assert out["integrations"]["openai_local"] is True
    assert out["integrations"]["openai_hosted"] is False


@pytest.mark.parametrize(
    ("base_url", "server_type"),
    [
        ("https://api.openai.com/v1", "auto"),
        ("https://openrouter.ai/api/v1", "auto"),
        ("http://my-gateway:8080/v1", "openai"),
        ("http://my-gateway:8080/v1", "openrouter"),
    ],
)
def test_openai_hosted(settings_store, db: Path, base_url: str, server_type: str):
    _set(
        settings_store,
        llm_provider="openai",
        openai_base_url=base_url,
        openai_model="m",
        openai_server_type=server_type,
    )
    out = _provider_out(db)
    assert out["integrations"]["openai_hosted"] is True
    assert out["integrations"]["openai_local"] is False


@pytest.mark.parametrize(
    ("base_url", "model"),
    [
        ("", "m"),
        ("   ", "m"),
        ("http://unsloth:8000/v1", ""),
        ("http://unsloth:8000/v1", "  "),
    ],
)
def test_openai_incomplete_reports_neither(settings_store, db: Path, base_url: str, model: str):
    _set(
        settings_store,
        llm_provider="openai",
        openai_base_url=base_url,
        openai_model=model,
    )
    out = _provider_out(db)
    assert out["integrations"]["openai_local"] is False
    assert out["integrations"]["openai_hosted"] is False


def test_values_are_bools(settings_store, db: Path):
    cases = [
        {"llm_provider": "ollama", "openai_base_url": "http://unsloth:8000/v1", "openai_model": "m"},
        {
            "llm_provider": "openai",
            "openai_base_url": "http://unsloth:8000/v1",
            "openai_model": "m",
            "openai_server_type": "auto",
        },
        {
            "llm_provider": "openai",
            "openai_base_url": "https://api.openai.com/v1",
            "openai_model": "m",
            "openai_server_type": "auto",
        },
        {"llm_provider": "openai", "openai_base_url": "", "openai_model": "m"},
    ]
    for case in cases:
        _set(settings_store, **case)
        out = _provider_out(db)
        for key in ("openai_local", "openai_hosted"):
            assert type(out["integrations"][key]) is bool


def test_other_integration_keys_unchanged(settings_store, db: Path):
    _set(
        settings_store,
        llm_provider="openai",
        openai_base_url="http://unsloth:8000/v1",
        openai_model="m",
        openai_server_type="auto",
    )
    out = _provider_out(db, SimpleNamespace(reachable=False))
    assert set(out["integrations"]) == {
        "bazarr",
        "sonarr",
        "radarr",
        "tautulli",
        "plex",
        "ollama",
        "openai_local",
        "openai_hosted",
    }
    assert out["integrations"]["ollama"] is False


def test_payload_has_no_llm_secrets(settings_store, db: Path):
    _set(
        settings_store,
        llm_provider="openai",
        openai_base_url="http://secret-host/v1",
        openai_api_key="abc-not-a-key",
        openai_model="m-secret",
        openai_server_type="local",
    )
    text = json.dumps(_full_payload(db))
    assert "secret-host" not in text
    assert "abc-not-a-key" not in text
    assert "m-secret" not in text
    assert "/v1" not in text
    assert '"openai_local": true' in text


def test_no_new_top_level_key(settings_store, db: Path):
    _set(
        settings_store,
        llm_provider="openai",
        openai_base_url="http://secret-host/v1",
        openai_api_key="abc-not-a-key",
        openai_model="m-secret",
        openai_server_type="local",
    )
    payload = _full_payload(db)
    for key in payload:
        assert "llm" not in key.lower()
        assert "openai" not in key.lower()
