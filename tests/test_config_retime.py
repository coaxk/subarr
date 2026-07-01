"""#359: SUBARR_RETIME_ENABLED flag. The bake — a sweep over the live 1801-sub
corpus proved the re-timer (target_cps=17, min_cue_ms=1000) cuts critical-CPS
cues 22.9%->5.2% and unreadable micro-cues 122739->27824 with zero new overlaps
(all gap-clamped) — so it now ships ON by default. Opt out with
SUBARR_RETIME_ENABLED=0."""

from __future__ import annotations

import importlib

from subarr import config


def test_retime_enabled_defaults_on(monkeypatch):
    monkeypatch.delenv("SUBARR_RETIME_ENABLED", raising=False)
    importlib.reload(config)
    assert config.settings.retime_enabled is True


def test_retime_enabled_off_via_env(monkeypatch):
    monkeypatch.setenv("SUBARR_RETIME_ENABLED", "0")
    importlib.reload(config)
    assert config.settings.retime_enabled is False
    monkeypatch.setenv("SUBARR_RETIME_ENABLED", "false")
    importlib.reload(config)
    assert config.settings.retime_enabled is False


def test_retime_enabled_on_via_env(monkeypatch):
    monkeypatch.setenv("SUBARR_RETIME_ENABLED", "1")
    importlib.reload(config)
    assert config.settings.retime_enabled is True
    monkeypatch.setenv("SUBARR_RETIME_ENABLED", "true")
    importlib.reload(config)
    assert config.settings.retime_enabled is True
