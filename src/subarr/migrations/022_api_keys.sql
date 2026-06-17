-- 022_api_keys.sql
--
-- #259 user-managed API keys. Multi-row (unlike the single-row auth_credential):
-- an admin mints as many labelled keys as they like. Keys are high-entropy, so
-- we store an unsalted SHA-256 (token_hash) — the UNIQUE index makes per-request
-- verification an O(1) indexed lookup. The plaintext is never stored; last4 is
-- kept only for masked display (sbar_…WXYZ).
CREATE TABLE IF NOT EXISTS api_key (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    label         TEXT NOT NULL,
    token_hash    TEXT NOT NULL UNIQUE,
    last4         TEXT NOT NULL,
    created_at    REAL NOT NULL,
    last_used_at  REAL
);
