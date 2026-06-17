"""#259: /api/auth/keys CRUD endpoints (gate disabled in the test env)."""

from __future__ import annotations


def test_create_returns_token_once(app_with_stub):
    r = app_with_stub.post("/api/auth/keys", json={"label": "home-assistant"})
    assert r.status_code == 201
    body = r.json()
    assert body["token"].startswith("sbar_")
    assert body["label"] == "home-assistant"
    assert body["last4"] == body["token"][-4:]
    assert isinstance(body["id"], int)


def test_list_carries_no_secret(app_with_stub):
    app_with_stub.post("/api/auth/keys", json={"label": "scripts"})
    r = app_with_stub.get("/api/auth/keys")
    assert r.status_code == 200
    keys = r.json()["keys"]
    assert any(k["label"] == "scripts" for k in keys)
    for k in keys:
        assert "token" not in k
        assert "token_hash" not in k


def test_empty_label_rejected(app_with_stub):
    r = app_with_stub.post("/api/auth/keys", json={"label": "   "})
    assert r.status_code == 422


def test_delete_then_gone(app_with_stub):
    kid = app_with_stub.post("/api/auth/keys", json={"label": "temp"}).json()["id"]
    assert app_with_stub.delete(f"/api/auth/keys/{kid}").status_code == 204
    keys = app_with_stub.get("/api/auth/keys").json()["keys"]
    assert all(k["id"] != kid for k in keys)


def test_delete_missing_is_404(app_with_stub):
    assert app_with_stub.delete("/api/auth/keys/999999").status_code == 404
