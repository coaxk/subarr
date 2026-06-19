"""#292 auth-hardening tests: password cap, timing equalization, setup warn-log.

Fix 1 — password max_length=1024: a >1024-char password is rejected by
  pydantic before touching pbkdf2 (422 on both /setup and /login).
Fix 2 — verify_login timing equalization: returns the correct bool for
  unknown username, wrong password, and correct credentials; never
  short-circuits on the unhappy path (dummy PBKDF2 keeps timing honest).
Fix 3 — setup warn-log: /setup still 201 on first call, 409 on second.
"""

from __future__ import annotations

import logging
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from subarr.auth import hash_password, verify_login
from subarr.auth_store import AuthStore
from subarr.migrate import run_migrations
from subarr.routers import auth as auth_router


# ─── shared app builder (mirrors test_auth_router.py) ─────────────────────────


def _app(tmp_path, *, auth_user="", auth_pass="", api_key=""):
    db = tmp_path / "a.db"
    run_migrations(db)
    app = FastAPI()
    app.state.auth_store = AuthStore(db)
    app.state.settings = types.SimpleNamespace(auth_user=auth_user, auth_pass=auth_pass, api_key=api_key)
    app.add_middleware(SessionMiddleware, secret_key="test-secret")
    app.include_router(auth_router.router)
    return app


@pytest.fixture
def client(tmp_path):
    return TestClient(_app(tmp_path))


# ─── Fix 1: password max_length cap ───────────────────────────────────────────


def test_login_rejects_oversized_password(client):
    """A password longer than 1024 chars must be rejected with 422 by pydantic
    before the request ever reaches pbkdf2 — no CPU-DoS on unauthenticated POST."""
    big = "x" * 1025
    r = client.post("/api/auth/login", json={"username": "admin", "password": big})
    assert r.status_code == 422


def test_setup_rejects_oversized_password(client):
    """Same cap applies to /setup — POST body validation rejects before hashing."""
    big = "x" * 1025
    r = client.post("/api/auth/setup", json={"username": "admin", "password": big})
    assert r.status_code == 422


def test_login_accepts_exactly_1024_char_password(tmp_path):
    """Boundary: exactly 1024 chars should be accepted (not rejected)."""
    app = _app(tmp_path)
    c = TestClient(app)
    # First set up an account with a 1024-char password
    pw_1024 = "a" * 1024
    r = c.post("/api/auth/setup", json={"username": "admin", "password": pw_1024})
    assert r.status_code == 201
    c.post("/api/auth/logout")
    r = c.post("/api/auth/login", json={"username": "admin", "password": pw_1024})
    assert r.status_code == 200


# ─── Fix 2: verify_login timing equalization ──────────────────────────────────


def _settings(**kw):
    base = dict(auth_user="", auth_pass="", api_key="")
    base.update(kw)
    return types.SimpleNamespace(**base)


class _FakeStore:
    """Minimal AuthStore stub for verify_login unit tests."""

    def __init__(self, credential=None):
        self._cred = credential

    def get_credential(self):
        return self._cred


def test_verify_login_unknown_username_returns_false():
    """Unknown username must return False (not raise, not short-circuit)."""
    store = _FakeStore(credential=None)  # no stored credential at all
    result = verify_login("ghost", "anypassword", store=store, settings=_settings())
    assert result is False


def test_verify_login_wrong_password_returns_false():
    """Known username + wrong password → False."""
    h, salt, iters = hash_password("correctpassword")
    store = _FakeStore(
        credential={
            "username": "admin",
            "password_hash": h,
            "salt": salt,
            "iterations": iters,
        }
    )
    result = verify_login("admin", "wrongpassword", store=store, settings=_settings())
    assert result is False


def test_verify_login_correct_creds_returns_true():
    """Correct username + password → True."""
    h, salt, iters = hash_password("mypassword")
    store = _FakeStore(
        credential={
            "username": "admin",
            "password_hash": h,
            "salt": salt,
            "iterations": iters,
        }
    )
    result = verify_login("admin", "mypassword", store=store, settings=_settings())
    assert result is True


def test_verify_login_env_override_path_works():
    """Env-cred path (recovery) still returns True on exact match."""
    store = _FakeStore(credential=None)
    result = verify_login(
        "recover",
        "recoverpass",
        store=store,
        settings=_settings(auth_user="recover", auth_pass="recoverpass"),
    )
    assert result is True


def test_verify_login_no_cred_no_env_returns_false():
    """Totally unconfigured state: no stored cred, no env → False for anything."""
    store = _FakeStore(credential=None)
    result = verify_login("anyone", "anything", store=store, settings=_settings())
    assert result is False


# ─── Fix 3: setup warn-log ────────────────────────────────────────────────────


def test_setup_endpoint_201_on_first_call(client):
    """Setup works normally — 201 and account created."""
    r = client.post("/api/auth/setup", json={"username": "admin", "password": "supersecret"})
    assert r.status_code == 201
    assert r.json()["username"] == "admin"


def test_setup_endpoint_409_on_second_call(client):
    """Second setup call (race or repeat) returns 409 — already configured."""
    client.post("/api/auth/setup", json={"username": "admin", "password": "supersecret"})
    r = client.post("/api/auth/setup", json={"username": "other", "password": "supersecret"})
    assert r.status_code == 409


def test_setup_emits_warning_log(tmp_path, caplog):
    """Every POST to /setup should emit a WARNING log about the setup invocation."""
    app = _app(tmp_path)
    c = TestClient(app)
    with caplog.at_level(logging.WARNING, logger="subarr.routers.auth"):
        c.post("/api/auth/setup", json={"username": "admin", "password": "supersecret"})
    assert any("setup" in r.message.lower() for r in caplog.records), (
        "Expected a WARNING log mentioning 'setup' from subarr.routers.auth"
    )
