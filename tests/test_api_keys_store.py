"""#259: AuthStore methods for managed API keys."""

from __future__ import annotations

import pytest

from subarr.api_keys import generate_key
from subarr.auth_store import AuthStore
from subarr.migrate import run_migrations


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "subarr.db"
    run_migrations(db)
    s = AuthStore(db)
    yield s
    s.close()


def _mk(store, label="ci"):
    token, token_hash, last4 = generate_key()
    kid = store.create_api_key(label, token_hash, last4)
    return token, kid


def test_create_and_list(store):
    token, kid = _mk(store, "home-assistant")
    rows = store.list_api_keys()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == kid
    assert row["label"] == "home-assistant"
    assert row["last4"] == token[-4:]
    assert row["created_at"] is not None


def test_list_never_exposes_hash(store):
    _mk(store)
    row = store.list_api_keys()[0]
    assert "token_hash" not in row


def test_verify_hit_returns_row(store):
    token, kid = _mk(store, "scripts")
    row = store.verify_api_key(token)
    assert row is not None
    assert row["id"] == kid
    assert row["label"] == "scripts"


def test_verify_miss_returns_none(store):
    _mk(store)
    assert store.verify_api_key("sbar_not-a-real-key") is None


def test_verify_touches_last_used(store):
    token, _ = _mk(store)
    assert store.list_api_keys()[0]["last_used_at"] is None
    store.verify_api_key(token, now=1000.0)
    assert store.list_api_keys()[0]["last_used_at"] == 1000.0


def test_last_used_touch_is_throttled(store):
    token, _ = _mk(store)
    store.verify_api_key(token, now=1000.0)
    # within 60s → not re-written
    store.verify_api_key(token, now=1030.0)
    assert store.list_api_keys()[0]["last_used_at"] == 1000.0
    # past the window → updated
    store.verify_api_key(token, now=1100.0)
    assert store.list_api_keys()[0]["last_used_at"] == 1100.0


def test_delete(store):
    _, kid = _mk(store)
    assert store.delete_api_key(kid) is True
    assert store.list_api_keys() == []
    # second delete is a no-op miss
    assert store.delete_api_key(kid) is False
