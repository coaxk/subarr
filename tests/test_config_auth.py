"""#238 Phase A: auth-related config flags."""

from __future__ import annotations

from subarr import config


def _load(monkeypatch, **env):
    for k in ("SUBARR_AUTH_DISABLED", "SUBARR_AUTH_RESET", "SUBARR_COOKIE_SAMESITE"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return config.load()


def test_auth_flags_default_off(monkeypatch):
    s = _load(monkeypatch)
    assert s.auth_disabled is False
    assert s.auth_reset is False
    assert s.cookie_samesite == "lax"


def test_auth_disabled_and_reset_truthy(monkeypatch):
    s = _load(monkeypatch, SUBARR_AUTH_DISABLED="1", SUBARR_AUTH_RESET="true")
    assert s.auth_disabled is True
    assert s.auth_reset is True


def test_cookie_samesite_normalized(monkeypatch):
    assert _load(monkeypatch, SUBARR_COOKIE_SAMESITE="None").cookie_samesite == "none"
    assert _load(monkeypatch, SUBARR_COOKIE_SAMESITE="Strict").cookie_samesite == "strict"


def test_cookie_samesite_invalid_falls_back_to_lax(monkeypatch):
    assert _load(monkeypatch, SUBARR_COOKIE_SAMESITE="bogus").cookie_samesite == "lax"
