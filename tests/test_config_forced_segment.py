"""#364 slice 1 — SUBARR_FORCED_SEGMENT_ENABLED. OFF by default (the skip-English
optimisation stays intact); env-togglable; mirrors test_config_retime.py."""

from __future__ import annotations

import importlib

from subarr import config


def test_forced_segment_defaults_off(monkeypatch):
    monkeypatch.delenv("SUBARR_FORCED_SEGMENT_ENABLED", raising=False)
    importlib.reload(config)
    assert config.settings.forced_segment_enabled is False


def test_forced_segment_on_via_env(monkeypatch):
    monkeypatch.setenv("SUBARR_FORCED_SEGMENT_ENABLED", "1")
    importlib.reload(config)
    assert config.settings.forced_segment_enabled is True
    monkeypatch.setenv("SUBARR_FORCED_SEGMENT_ENABLED", "true")
    importlib.reload(config)
    assert config.settings.forced_segment_enabled is True


def test_forced_segment_off_via_env(monkeypatch):
    monkeypatch.setenv("SUBARR_FORCED_SEGMENT_ENABLED", "0")
    importlib.reload(config)
    assert config.settings.forced_segment_enabled is False
