"""#238: storage for the single admin credential + the session-signing secret.

Same SQLite file as the rest of subarr. WAL + a Lock around the connection
(mirrors the other stores). Single-row model keyed at id=1 so first-run setup is
an atomic INSERT OR IGNORE — a boot-time burst of requests can't double-create.
"""

from __future__ import annotations

import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .data_persistence import apply_journal_mode


# DDL for the session-secret table, duplicated from migration 021 so the
# import-time bootstrap below can run BEFORE run_migrations (the SessionMiddleware
# resolves its secret at import; migrations run later, in the lifespan). Both are
# `CREATE TABLE IF NOT EXISTS`, so they coexist harmlessly.
_AUTH_SECRET_DDL = (
    "CREATE TABLE IF NOT EXISTS auth_secret ("
    " id INTEGER PRIMARY KEY CHECK (id = 1), secret TEXT NOT NULL, created_at REAL NOT NULL)"
)


def load_or_create_session_secret(db_path) -> str | None:
    """Return the persisted session-signing secret, creating it (and its table)
    if absent. Used at import to seed SessionMiddleware so logins survive
    restarts and --reload. Returns None if the DB can't be opened (e.g. importing
    on a host without the data dir, or in a read-only context) — the caller then
    falls back to an ephemeral per-boot secret rather than crashing at import."""
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            apply_journal_mode(conn, db_path)
            conn.execute(_AUTH_SECRET_DDL)
            row = conn.execute("SELECT secret FROM auth_secret WHERE id = 1").fetchone()
            if row is not None:
                return row[0]
            conn.execute(
                "INSERT OR IGNORE INTO auth_secret (id, secret, created_at) VALUES (1, ?, ?)",
                (secrets.token_urlsafe(48), time.time()),
            )
            conn.commit()
            return conn.execute("SELECT secret FROM auth_secret WHERE id = 1").fetchone()[0]
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — any DB failure → caller uses ephemeral
        return None


class AuthStore:
    def __init__(self, db_path: Path):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        apply_journal_mode(self._conn, db_path)

    def get_or_create_secret(self) -> str:
        """The session-signing secret, generated once and persisted so sessions
        survive a restart. Race-safe: INSERT OR IGNORE then re-read the winner."""
        with self._lock:
            row = self._conn.execute("SELECT secret FROM auth_secret WHERE id = 1").fetchone()
            if row is not None:
                return row["secret"]
            self._conn.execute(
                "INSERT OR IGNORE INTO auth_secret (id, secret, created_at) VALUES (1, ?, ?)",
                (secrets.token_urlsafe(48), time.time()),
            )
            self._conn.commit()
            return self._conn.execute("SELECT secret FROM auth_secret WHERE id = 1").fetchone()["secret"]

    def has_credential(self) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM auth_credential WHERE id = 1").fetchone() is not None

    def get_credential(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT username, password_hash, salt, iterations FROM auth_credential WHERE id = 1"
            ).fetchone()
            return dict(row) if row else None

    def set_credential(self, username: str, password_hash: str, salt: str, iterations: int) -> None:
        """Create or replace the admin credential (used by set-password / CLI)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO auth_credential (id, username, password_hash, salt, iterations, created_at) "
                "VALUES (1, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET username=excluded.username, "
                "  password_hash=excluded.password_hash, salt=excluded.salt, iterations=excluded.iterations",
                (username, password_hash, salt, iterations, time.time()),
            )
            self._conn.commit()

    def create_credential_if_absent(
        self, username: str, password_hash: str, salt: str, iterations: int
    ) -> bool:
        """Atomic first-run create. True if THIS call created it; False if one
        already existed (the loser of a concurrent /setup race)."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO auth_credential "
                "(id, username, password_hash, salt, iterations, created_at) VALUES (1, ?, ?, ?, ?, ?)",
                (username, password_hash, salt, iterations, time.time()),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def clear_credential(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM auth_credential WHERE id = 1")
            self._conn.commit()

    # ─── #259 managed API keys ──────────────────────────────────────
    # Multi-row, unlike the single admin credential. Tokens are high-entropy, so
    # they are stored as an unsalted SHA-256 (token_hash, UNIQUE → indexed
    # lookup). The plaintext is never persisted.

    # last_used_at is only re-written when the previous touch is older than this,
    # so a polling client can't write to SQLite on every request.
    _LAST_USED_THROTTLE_S = 60.0

    def create_api_key(self, label: str, token_hash: str, last4: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO api_key (label, token_hash, last4, created_at) VALUES (?, ?, ?, ?)",
                (label, token_hash, last4, time.time()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_api_keys(self) -> list[dict[str, Any]]:
        """Metadata only — never returns token_hash."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, label, last4, created_at, last_used_at FROM api_key ORDER BY created_at"
            ).fetchall()
            return [dict(r) for r in rows]

    def verify_api_key(self, token: str, *, now: float | None = None) -> dict[str, Any] | None:
        """Hash the presented token, look it up (indexed), and on a hit record a
        throttled last_used_at. Returns {id, label} or None. Never raises."""
        from .api_keys import hash_key

        ts = time.time() if now is None else now
        with self._lock:
            row = self._conn.execute(
                "SELECT id, label, last_used_at FROM api_key WHERE token_hash = ?",
                (hash_key(token),),
            ).fetchone()
            if row is None:
                return None
            last_used = row["last_used_at"]
            if last_used is None or (ts - last_used) >= self._LAST_USED_THROTTLE_S:
                self._conn.execute("UPDATE api_key SET last_used_at = ? WHERE id = ?", (ts, row["id"]))
                self._conn.commit()
            return {"id": row["id"], "label": row["label"]}

    def delete_api_key(self, key_id: int) -> bool:
        """True if a row was removed (revoke)."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM api_key WHERE id = ?", (key_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def close(self) -> None:
        self._conn.close()
