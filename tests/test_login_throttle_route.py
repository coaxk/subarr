"""#260: the throttle as wired into POST /api/auth/login (gate disabled in tests)."""

from __future__ import annotations

from subarr.auth import hash_password


def _bad_login(client):
    return client.post("/api/auth/login", json={"username": "x", "password": "wrong"})


def test_login_blocks_after_max_attempts(app_with_stub):
    # Default max_attempts=5: the first 5 fail (401), the 6th is throttled (429).
    statuses = [_bad_login(app_with_stub).status_code for _ in range(6)]
    assert statuses[:5] == [401, 401, 401, 401, 401]
    last = app_with_stub.post("/api/auth/login", json={"username": "x", "password": "wrong"})
    assert last.status_code == 429
    assert "Retry-After" in last.headers
    assert int(last.headers["Retry-After"]) > 0


def test_throttle_config_endpoint(app_with_stub):
    r = app_with_stub.get("/api/auth/throttle-config")
    assert r.status_code == 200
    body = r.json()
    assert body["max_attempts"] == 5
    assert body["window_s"] == 300
    assert body["trusted_proxies"] == []
    assert body["allowlist"] == []


def test_successful_login_resets_the_counter(app_with_stub):
    # Seed a real credential so a correct login can succeed.
    h, salt, iters = hash_password("correct-horse-battery")
    app_with_stub.app.state.auth_store.set_credential("admin", h, salt, iters)

    for _ in range(4):  # under the threshold
        assert _bad_login(app_with_stub).status_code == 401
    ok = app_with_stub.post(
        "/api/auth/login", json={"username": "admin", "password": "correct-horse-battery"}
    )
    assert ok.status_code == 200
    # Counter cleared on success → a subsequent wrong attempt is 401, not 429.
    assert _bad_login(app_with_stub).status_code == 401
