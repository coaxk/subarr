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


class AuthStore:
    def __init__(self, db_path: Path):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")

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

    def close(self) -> None:
        self._conn.close()
