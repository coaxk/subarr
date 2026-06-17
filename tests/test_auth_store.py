"""#238 Phase A: auth_store (credential + session secret)."""

from __future__ import annotations

from subarr.auth_store import AuthStore
from subarr.migrate import run_migrations


def _store(tmp_path):
    db = tmp_path / "a.db"
    run_migrations(db)
    return AuthStore(db)


def test_secret_persists(tmp_path):
    s = _store(tmp_path)
    first = s.get_or_create_secret()
    assert first
    assert s.get_or_create_secret() == first  # stable, not regenerated


def test_credential_lifecycle(tmp_path):
    s = _store(tmp_path)
    assert s.has_credential() is False
    s.set_credential("admin", "hash", "salt", 200000)
    assert s.has_credential() is True
    cred = s.get_credential()
    assert cred == {"username": "admin", "password_hash": "hash", "salt": "salt", "iterations": 200000}


def test_set_credential_replaces(tmp_path):
    s = _store(tmp_path)
    s.set_credential("admin", "h1", "s1", 1)
    s.set_credential("admin", "h2", "s2", 2)
    assert s.get_credential()["password_hash"] == "h2"  # single row, last write wins


def test_create_if_absent_is_atomic(tmp_path):
    s = _store(tmp_path)
    assert s.create_credential_if_absent("admin", "h", "s", 1) is True
    assert s.create_credential_if_absent("attacker", "h2", "s2", 1) is False  # already exists
    assert s.get_credential()["username"] == "admin"  # first write stands


def test_clear_credential(tmp_path):
    s = _store(tmp_path)
    s.set_credential("admin", "h", "s", 1)
    s.clear_credential()
    assert s.has_credential() is False
