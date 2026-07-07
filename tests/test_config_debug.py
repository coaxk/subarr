"""#157 gap-fill: SUBARR_DEBUG verbose knob. Off by default (today's INFO
behaviour byte-for-byte); on via 1/true/yes/on. Mirrors test_config_retime."""

from __future__ import annotations

import importlib

from subarr import config


def test_debug_defaults_off(monkeypatch):
    monkeypatch.delenv("SUBARR_DEBUG", raising=False)
    importlib.reload(config)
    assert config.settings.debug is False


def test_debug_off_via_blank(monkeypatch):
    # A blank line in .env (SUBARR_DEBUG=) must count as off, not on.
    monkeypatch.setenv("SUBARR_DEBUG", "")
    importlib.reload(config)
    assert config.settings.debug is False


def test_debug_on_via_env(monkeypatch):
    monkeypatch.setenv("SUBARR_DEBUG", "1")
    importlib.reload(config)
    assert config.settings.debug is True
    monkeypatch.setenv("SUBARR_DEBUG", "true")
    importlib.reload(config)
    assert config.settings.debug is True
    monkeypatch.setenv("SUBARR_DEBUG", "on")
    importlib.reload(config)
    assert config.settings.debug is True


def test_debug_off_via_env(monkeypatch):
    monkeypatch.setenv("SUBARR_DEBUG", "0")
    importlib.reload(config)
    assert config.settings.debug is False
    monkeypatch.setenv("SUBARR_DEBUG", "false")
    importlib.reload(config)
    assert config.settings.debug is False
