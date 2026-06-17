"""#238 Phase A: auth router (state / setup / login / logout)."""

from __future__ import annotations

import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from subarr.auth_store import AuthStore
from subarr.migrate import run_migrations
from subarr.routers import auth as auth_router


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


def test_state_fresh_needs_setup(client):
    body = client.get("/api/auth/state").json()
    assert body == {"needs_setup": True, "authed": False, "username": None}


def test_setup_then_authed(client):
    r = client.post("/api/auth/setup", json={"username": "admin", "password": "supersecret"})
    assert r.status_code == 201 and r.json()["username"] == "admin"
    st = client.get("/api/auth/state").json()
    assert st["needs_setup"] is False and st["authed"] is True and st["username"] == "admin"


def test_setup_rejects_second_call(client):
    client.post("/api/auth/setup", json={"username": "admin", "password": "supersecret"})
    r = client.post("/api/auth/setup", json={"username": "x", "password": "anothersecret"})
    assert r.status_code == 409


def test_setup_rejects_short_password(client):
    r = client.post("/api/auth/setup", json={"username": "admin", "password": "short"})
    assert r.status_code == 422


def test_login_wrong_is_generic_401(client):
    client.post("/api/auth/setup", json={"username": "admin", "password": "supersecret"})
    client.post("/api/auth/logout")
    r = client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    assert r.status_code == 401 and r.json()["detail"] == "invalid username or password"
    # unknown user → SAME response (no enumeration)
    r2 = client.post("/api/auth/login", json={"username": "ghost", "password": "nope"})
    assert r2.status_code == 401 and r2.json()["detail"] == "invalid username or password"


def test_login_success_and_logout(client):
    client.post("/api/auth/setup", json={"username": "admin", "password": "supersecret"})
    client.post("/api/auth/logout")
    assert client.get("/api/auth/state").json()["authed"] is False
    assert (
        client.post("/api/auth/login", json={"username": "admin", "password": "supersecret"}).status_code
        == 200
    )
    assert client.get("/api/auth/state").json()["authed"] is True
    client.post("/api/auth/logout")
    assert client.get("/api/auth/state").json()["authed"] is False


def test_env_override_login_works_for_recovery(tmp_path):
    # no stored cred; env SUBARR_USER/PASS set → login via env (recovery path)
    app = _app(tmp_path, auth_user="recover", auth_pass="recoverpass")
    c = TestClient(app)
    assert c.get("/api/auth/state").json()["needs_setup"] is False  # env ⇒ not setup
    assert (
        c.post("/api/auth/login", json={"username": "recover", "password": "recoverpass"}).status_code == 200
    )


def test_state_not_setup_when_env_api_key(tmp_path):
    app = _app(tmp_path, api_key="abc123")
    assert TestClient(app).get("/api/auth/state").json()["needs_setup"] is False
