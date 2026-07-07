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


def test_apply_logging_debug_on_raises_root_and_unpins_httpx(monkeypatch):
    import logging

    from subarr import app as app_mod

    importlib.reload(config)  # ensure settings singleton is fresh
    # Restore INFO baseline first so the assertion isn't order-dependent.
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    app_mod._apply_logging(debug=True)
    assert logging.getLogger().level == logging.DEBUG
    # httpx un-pinned (left at INFO, not WARNING) so request detail shows.
    assert logging.getLogger("httpx").level == logging.INFO
    assert logging.getLogger("httpcore").level == logging.INFO


def test_apply_logging_debug_off_is_todays_behaviour(monkeypatch):
    import logging

    from subarr import app as app_mod

    app_mod._apply_logging(debug=False)
    assert logging.getLogger().level == logging.INFO
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
