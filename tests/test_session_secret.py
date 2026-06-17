"""#238 follow-up: persist the session-signing secret so logins survive
restarts AND uvicorn --reload (the secret was ephemeral, silently logging
everyone out on every restart/reload)."""

from __future__ import annotations

from subarr.auth_store import load_or_create_session_secret


def test_creates_and_persists_secret(tmp_path):
    db = tmp_path / "subarr.db"
    # No migrations run yet — the helper must bootstrap the table itself
    # (it resolves at import, before run_migrations).
    s1 = load_or_create_session_secret(db)
    assert isinstance(s1, str) and len(s1) >= 32
    # Second call (and a fresh process would do the same) returns the SAME value.
    s2 = load_or_create_session_secret(db)
    assert s1 == s2


def test_returns_none_when_db_unreachable():
    # A path under a nonexistent directory can't be opened → None, so the
    # caller falls back to an ephemeral secret instead of crashing at import.
    bad = "/nonexistent-dir-xyz-12345/sub.db"
    assert load_or_create_session_secret(bad) is None


def test_matches_authstore_secret(tmp_path):
    # The bootstrap helper and AuthStore.get_or_create_secret share one row,
    # so the secret is consistent however it's first created.
    from subarr.auth_store import AuthStore
    from subarr.migrate import run_migrations

    db = tmp_path / "subarr.db"
    boot = load_or_create_session_secret(db)
    run_migrations(db)
    store = AuthStore(db)
    assert store.get_or_create_secret() == boot
    store.close()
