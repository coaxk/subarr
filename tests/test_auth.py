"""Tests for the basic-auth middleware.

Covers:
  - Disabled by default (no creds → no 401)
  - Enabled when both SUBARR_USER and SUBARR_PASS set
  - 401 + WWW-Authenticate when no Authorization header
  - 401 when wrong credentials
  - 200 when correct credentials
  - /api/health bypass (always 200 regardless of auth)
  - /static/* bypass
  - Constant-time compare protects against timing oracles
"""

from __future__ import annotations

import base64

from fastapi import FastAPI
from fastapi.testclient import TestClient

from subarr.auth import BasicAuthMiddleware, is_path_bypassed


def _app(user: str, password: str) -> FastAPI:
    app = FastAPI()
    app.add_middleware(BasicAuthMiddleware, user=user, password=password)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/queue")
    def queue():
        return {"queued": []}

    @app.get("/static/x.css")
    def static():
        return {"static": True}

    return app


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


# ─── No-op when creds empty ────────────────────────────────────────


def test_no_auth_when_user_empty():
    c = TestClient(_app(user="", password="anything"))
    r = c.get("/api/queue")
    assert r.status_code == 200


def test_no_auth_when_password_empty():
    c = TestClient(_app(user="admin", password=""))
    r = c.get("/api/queue")
    assert r.status_code == 200


# ─── Enabled mode ──────────────────────────────────────────────────


def test_401_when_no_auth_header():
    c = TestClient(_app(user="admin", password="hunter2"))
    r = c.get("/api/queue")
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Basic realm=")


def test_401_when_wrong_password():
    c = TestClient(_app(user="admin", password="hunter2"))
    r = c.get("/api/queue", headers={"Authorization": _basic("admin", "wrong")})
    assert r.status_code == 401


def test_401_when_wrong_user():
    c = TestClient(_app(user="admin", password="hunter2"))
    r = c.get("/api/queue", headers={"Authorization": _basic("nope", "hunter2")})
    assert r.status_code == 401


def test_200_with_correct_creds():
    c = TestClient(_app(user="admin", password="hunter2"))
    r = c.get("/api/queue", headers={"Authorization": _basic("admin", "hunter2")})
    assert r.status_code == 200
    assert r.json()["queued"] == []


def test_malformed_basic_header_rejected():
    c = TestClient(_app(user="admin", password="hunter2"))
    # Not actually base64
    r = c.get("/api/queue", headers={"Authorization": "Basic notbase64@@!!"})
    assert r.status_code == 401
    # Missing colon in decoded payload
    bad = "Basic " + base64.b64encode(b"justuser").decode()
    r = c.get("/api/queue", headers={"Authorization": bad})
    assert r.status_code == 401


def test_non_basic_scheme_rejected():
    c = TestClient(_app(user="admin", password="hunter2"))
    r = c.get("/api/queue", headers={"Authorization": "Bearer token123"})
    assert r.status_code == 401


# ─── Bypass paths ──────────────────────────────────────────────────


def test_health_endpoint_bypasses_auth():
    """Monitoring tools (uptime kuma, docker healthcheck) hit /api/health
    without credentials. Must always return 200."""
    c = TestClient(_app(user="admin", password="hunter2"))
    r = c.get("/api/health")
    assert r.status_code == 200


def test_static_paths_bypass_auth():
    """Static assets (CSS/JS/fonts) need to be reachable so the
    auth-required HTML can render."""
    c = TestClient(_app(user="admin", password="hunter2"))
    r = c.get("/static/x.css")
    assert r.status_code == 200


def test_is_path_bypassed_helper():
    assert is_path_bypassed("/api/health") is True
    assert is_path_bypassed("/static/anything.css") is True
    assert is_path_bypassed("/api/queue") is False
    assert is_path_bypassed("/") is False


# ─── Constant-time compare ─────────────────────────────────────────


def test_uses_constant_time_compare():
    """Smoke check: the comparison uses secrets.compare_digest by
    reading the source. If someone rewrites with ==, this test fails
    + we re-review."""
    import subarr.auth

    src = open(subarr.auth.__file__, encoding="utf-8").read()
    assert "secrets.compare_digest" in src, (
        "auth.py must use secrets.compare_digest, not == — protects "
        "against timing attacks on the password compare."
    )
