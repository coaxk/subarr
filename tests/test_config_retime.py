"""#359: SUBARR_RETIME_ENABLED flag — default off, opt-in until the params are
arena-proven (then the tuning slice flips the default)."""

from __future__ import annotations

import importlib

from subarr import config


def test_retime_enabled_defaults_off(monkeypatch):
    monkeypatch.delenv("SUBARR_RETIME_ENABLED", raising=False)
    importlib.reload(config)
    assert config.settings.retime_enabled is False


def test_retime_enabled_on_via_env(monkeypatch):
    monkeypatch.setenv("SUBARR_RETIME_ENABLED", "1")
    importlib.reload(config)
    assert config.settings.retime_enabled is True
    monkeypatch.setenv("SUBARR_RETIME_ENABLED", "true")
    importlib.reload(config)
    assert config.settings.retime_enabled is True
