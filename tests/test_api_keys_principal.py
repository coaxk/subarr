"""#259: current_principal accepts managed API keys alongside env key / session."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from subarr.api_keys import generate_key
from subarr.auth import current_principal
from subarr.auth_store import AuthStore
from subarr.migrate import run_migrations


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "subarr.db"
    run_migrations(db)
    s = AuthStore(db)
    yield s
    s.close()


def _settings(**over):
    base = dict(auth_user="", auth_pass="", api_key="")
    base.update(over)
    return SimpleNamespace(**base)


def _scope(*, header=None, query=b""):
    headers = []
    if header is not None:
        headers.append((b"x-api-key", header.encode()))
    return {"type": "http", "headers": headers, "query_string": query, "session": {}}


def test_valid_managed_key_authenticates(store):
    token, token_hash, last4 = generate_key()
    store.create_api_key("ci", token_hash, last4)
    p = current_principal(_scope(header=token), settings=_settings(), store=store)
    assert p == "key:ci"


def test_managed_key_via_query_param(store):
    token, token_hash, last4 = generate_key()
    store.create_api_key("scripts", token_hash, last4)
    p = current_principal(_scope(query=f"apikey={token}".encode()), settings=_settings(), store=store)
    assert p == "key:scripts"


def test_revoked_key_is_denied(store):
    token, token_hash, last4 = generate_key()
    kid = store.create_api_key("temp", token_hash, last4)
    store.delete_api_key(kid)
    assert current_principal(_scope(header=token), settings=_settings(), store=store) is None


def test_unknown_key_is_denied(store):
    assert current_principal(_scope(header="sbar_nope"), settings=_settings(), store=store) is None


def test_env_api_key_still_works_without_store():
    s = _settings(api_key="envkey")
    assert current_principal(_scope(header="envkey"), settings=s, store=None) == "api-key"
