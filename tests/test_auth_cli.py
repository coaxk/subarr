"""#238 Phase A: auth recovery CLI."""

from __future__ import annotations

import types

from subarr import cli
from subarr.auth import verify_login
from subarr.auth_store import AuthStore
from subarr.migrate import run_migrations


def _store(tmp_path):
    db = tmp_path / "a.db"
    run_migrations(db)
    return AuthStore(db)


def test_reset_auth_clears(tmp_path):
    s = _store(tmp_path)
    s.set_credential("admin", "h", "salt", 1)
    assert cli.reset_auth(s) == 0
    assert s.has_credential() is False


def test_set_password_sets_verifiable_cred(tmp_path):
    s = _store(tmp_path)
    assert cli.set_password(s, "admin", "supersecret") == 0
    settings = types.SimpleNamespace(auth_user="", auth_pass="")
    assert verify_login("admin", "supersecret", store=s, settings=settings) is True
    assert verify_login("admin", "wrong", store=s, settings=settings) is False


def test_set_password_rejects_short(tmp_path):
    s = _store(tmp_path)
    assert cli.set_password(s, "admin", "short") == 2
    assert s.has_credential() is False


def test_main_dispatches(tmp_path, monkeypatch):
    db = tmp_path / "a.db"
    run_migrations(db)
    monkeypatch.setattr("subarr.cli.settings", types.SimpleNamespace(db_path=db))
    assert cli.main(["set-password", "--username", "admin", "--password", "supersecret"]) == 0
    assert AuthStore(db).has_credential() is True
    assert cli.main(["reset-auth"]) == 0
    assert AuthStore(db).has_credential() is False
