-- 021_auth.sql
--
-- #238 forced auth. A single admin credential (fixed id=1) and a persisted
-- session-signing secret. The id=1 CHECK enforces the single-row model and lets
-- setup use an atomic INSERT OR IGNORE (no double-create under a boot race).
CREATE TABLE IF NOT EXISTS auth_credential (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    username      TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    salt          TEXT NOT NULL,
    iterations    INTEGER NOT NULL,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_secret (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    secret     TEXT NOT NULL,
    created_at REAL NOT NULL
);
