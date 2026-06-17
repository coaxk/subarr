"""#238 Phase A: password hashing, principal resolution, needs_setup, gate."""

from __future__ import annotations

import base64
import types

import pytest

from subarr import auth


def _settings(**over):
    base = dict(auth_user="", auth_pass="", api_key="", auth_disabled=False, cookie_samesite="lax")
    base.update(over)
    return types.SimpleNamespace(**base)


class _Store:
    def __init__(self, has=False):
        self._has = has

    def has_credential(self):
        return self._has


def _scope(headers=None, session=None, qs=b"", path="/"):
    return {
        "type": "http",
        "headers": headers or [],
        "session": session or {},
        "query_string": qs,
        "path": path,
    }


# ── hashing ──


def test_hash_verify_roundtrip():
    h, s, it = auth.hash_password("hunter2")
    assert auth.verify_password("hunter2", h, s, it) is True
    assert auth.verify_password("wrong", h, s, it) is False


def test_hash_salt_unique():
    h1, s1, _ = auth.hash_password("x")
    h2, s2, _ = auth.hash_password("x")
    assert s1 != s2 and h1 != h2


def test_verify_malformed_is_false_not_raise():
    assert auth.verify_password("x", "zz", "nothex", 1000) is False


# ── current_principal ──


def test_principal_session_wins():
    assert auth.current_principal(_scope(session={"user": "admin"}), settings=_settings()) == "admin"


def test_principal_env_basic_match():
    blob = base64.b64encode(b"admin:pw").decode()
    sc = _scope(headers=[(b"authorization", f"Basic {blob}".encode())])
    assert auth.current_principal(sc, settings=_settings(auth_user="admin", auth_pass="pw")) == "admin"


def test_principal_nonmatching_basic_falls_through():  # review #1
    # a reverse proxy passing a domain-level Basic cred downstream must be
    # IGNORED, never rejected — return None and let the gate decide.
    blob = base64.b64encode(b"proxyuser:proxypass").decode()
    sc = _scope(headers=[(b"authorization", f"Basic {blob}".encode())])
    assert auth.current_principal(sc, settings=_settings(auth_user="admin", auth_pass="pw")) is None
    # and a valid session on the SAME request still resolves
    sc2 = _scope(headers=[(b"authorization", f"Basic {blob}".encode())], session={"user": "admin"})
    assert auth.current_principal(sc2, settings=_settings(auth_user="admin", auth_pass="pw")) == "admin"


def test_principal_api_key_header_and_query():
    assert (
        auth.current_principal(_scope(headers=[(b"x-api-key", b"k3y")]), settings=_settings(api_key="k3y"))
        == "api-key"
    )
    assert auth.current_principal(_scope(qs=b"apikey=k3y"), settings=_settings(api_key="k3y")) == "api-key"


def test_principal_none_when_unconfigured():
    assert auth.current_principal(_scope(), settings=_settings()) is None


# ── needs_setup (review #4) ──


def test_needs_setup_true_when_nothing():
    assert auth.needs_setup(_settings(), _Store(has=False)) is True


def test_needs_setup_false_with_stored_cred():
    assert auth.needs_setup(_settings(), _Store(has=True)) is False


def test_needs_setup_false_with_env_api_key():
    assert auth.needs_setup(_settings(api_key="k"), _Store(has=False)) is False


def test_needs_setup_false_with_env_user():
    assert auth.needs_setup(_settings(auth_user="admin"), _Store(has=False)) is False


# ── AuthGateMiddleware ──


async def _run_gate(settings, store, scope):
    captured = {}
    inner_called = {"v": False}

    async def receive():
        return {"type": "http.request"}

    async def send(msg):
        if msg["type"] == "http.response.start":
            captured["status"] = msg["status"]
            captured["headers"] = dict(msg.get("headers") or [])

    async def inner(s, r, sd):
        inner_called["v"] = True

    await auth.AuthGateMiddleware(inner, settings=settings, store=store)(scope, receive, send)
    return inner_called["v"], captured


@pytest.mark.asyncio
async def test_gate_disabled_passes():
    called, _ = await _run_gate(_settings(auth_disabled=True), _Store(has=True), _scope(path="/api/mode"))
    assert called


@pytest.mark.asyncio
async def test_gate_bypasses_health_and_login():
    for p in ("/api/health", "/login", "/api/auth/state", "/static/x.js"):
        called, _ = await _run_gate(_settings(), _Store(has=True), _scope(path=p))
        assert called, p


@pytest.mark.asyncio
async def test_gate_principal_passes():
    called, _ = await _run_gate(
        _settings(), _Store(has=True), _scope(session={"user": "admin"}, path="/api/mode")
    )
    assert called


@pytest.mark.asyncio
async def test_gate_api_unauthed_401():
    called, cap = await _run_gate(_settings(), _Store(has=True), _scope(path="/api/mode"))
    assert not called and cap["status"] == 401


@pytest.mark.asyncio
async def test_gate_html_unauthed_redirects_login():
    called, cap = await _run_gate(_settings(), _Store(has=True), _scope(path="/"))
    assert not called and cap["status"] == 302 and cap["headers"][b"location"] == b"/login"


@pytest.mark.asyncio
async def test_gate_setup_mode_redirects_setup():
    called, cap = await _run_gate(_settings(), _Store(has=False), _scope(path="/"))
    assert not called and cap["status"] == 302 and cap["headers"][b"location"] == b"/setup"


@pytest.mark.asyncio
async def test_gate_setup_endpoint_open_only_in_setup_mode():
    called, _ = await _run_gate(_settings(), _Store(has=False), _scope(path="/api/auth/setup"))
    assert called  # reachable without auth while setup needed
    called2, cap = await _run_gate(_settings(), _Store(has=True), _scope(path="/api/auth/setup"))
    assert not called2 and cap["status"] == 401  # locked once a cred exists
