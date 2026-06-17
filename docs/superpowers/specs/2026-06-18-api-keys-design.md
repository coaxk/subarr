# Phase B — user-managed API keys (#259)

**Goal:** let an authenticated admin mint, list, and revoke long-lived API keys from the Settings page, so scripts/integrations can authenticate without the single env `SUBARR_API_KEY` or a browser session.

**Context:** Follow-up to the forced-auth roadmap (#238 / Phase A, shipped #258). The auth seam already accepts an api-key principal from the env value; this adds user-generated keys alongside it.

---

## Locked decisions (from brainstorm)

1. **Scope:** full access — a key authenticates as the admin, same access as a logged-in session. No per-key permission tiers (YAGNI; can be a later issue).
2. **Hashing:** fast **SHA-256, unsalted**. Keys are 256-bit random, so a slow KDF (pbkdf2) is unnecessary, and an unsalted deterministic hash lets us index the column and verify in O(1) per request. This is the GitHub/Stripe token model — *not* the password model (passwords are low-entropy, hence pbkdf2 there).
3. **Last-used tracking:** yes — best-effort `last_used_at`, throttled to ≤ once/60s per key, so the UI can distinguish a live key from a dead one before revoking.

---

## Key format & storage

- Token = `sbar_` + `secrets.token_urlsafe(32)` (~43 url-safe chars; 256 bits of entropy). The `sbar_` prefix makes keys greppable and recognizable in logs/configs.
- Shown **once**, in full, at creation. Never retrievable again — lose it ⇒ revoke + regenerate (documented; no plaintext recovery by design).
- Stored row holds: `label`, `token_hash` (SHA-256 hex, **UNIQUE**), `last4` (display), `created_at`, `last_used_at` (nullable). The plaintext and any reversible form are never persisted.
- UI displays `sbar_…WXYZ` using `last4`.

## Schema — `migrations/022_api_keys.sql`

```sql
CREATE TABLE IF NOT EXISTS api_key (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    label         TEXT NOT NULL,
    token_hash    TEXT NOT NULL UNIQUE,
    last4         TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    last_used_at  TEXT
);
```

`token_hash UNIQUE` gives an index for the per-request lookup. Timestamps are ISO-8601 UTC strings (matches the existing auth tables).

## Code units

- **`src/subarr/api_keys.py`** (new, pure, no DB) — easily unit-tested:
  - `generate_key() -> (token, token_hash, last4)` — mint a token, return it plus its hash and last-4.
  - `hash_key(token) -> str` — SHA-256 hex (the verify path hashes the presented key and looks it up).
- **`AuthStore`** (extend — it already owns the auth DB and is wired into the gate via `get_store`):
  - `create_api_key(label, token_hash, last4) -> int` (new row id)
  - `list_api_keys() -> list[dict]` — metadata only: id, label, last4, created_at, last_used_at. **Never** returns `token_hash`.
  - `verify_api_key(token, *, now=None) -> dict | None` — hash → indexed lookup; on hit, throttled `last_used_at` touch (skip if updated < 60s ago); returns the row (id, label) or None.
  - `delete_api_key(id) -> bool` — True if a row was removed.

## The auth seam

Extend `current_principal(scope, *, settings, store=None)` (backward-compatible default). After the existing env-`api_key` check, if a key was presented (`x-api-key` header or `?apikey=` query — reuse the existing extraction) and `store` is set, call `store.verify_api_key(key)`; on hit return `f"key:{row['label']}"`. The gate already resolves `store`, so it just passes it through. Env key and managed keys share the same presented-value extraction.

## Endpoints — `routers/api_keys.py`

All **behind** the auth gate (not in the bypass list), so only an authenticated principal reaches them:

- `GET  /api/auth/keys` → `{keys: [{id, label, last4, created_at, last_used_at}, ...]}`
- `POST /api/auth/keys` `{label}` → `201 {id, label, last4, created_at, token}` — `token` is the **full plaintext, returned exactly once**.
- `DELETE /api/auth/keys/{id}` → `204` on delete, `404` if absent.

Label: stripped, non-empty, ≤ 64 chars → `422` otherwise. Duplicate labels allowed (last4 disambiguates). A full-access key *can* call these endpoints — consistent with "full access = admin"; noted, not special-cased.

## UI — Settings page "API Keys" card

New card on the Settings page:
- Table: **Label · `sbar_…WXYZ` · Created · Last used · [Revoke]**. "Last used" shows relative time or "never".
- **Generate key:** enter a label → `POST` → show the full token once in a copy box with a *"copy it now — you won't see it again"* warning → list refreshes.
- **Revoke:** confirm → `DELETE` → list refreshes.
- Uses the `apiFetch` 401-helper from #258 for all calls.

## Error handling & edges

- Lost token → revoke + regenerate (no plaintext recovery).
- `DELETE` of a missing id → `404` (idempotent-friendly client can ignore).
- Env `SUBARR_API_KEY` keeps working unchanged, alongside managed keys.
- Empty/whitespace label rejected before any DB write.

## Testing

- `api_keys.py`: `generate_key` format (prefix, length, distinctness across calls), `hash_key` determinism.
- `AuthStore` methods: create; list returns metadata and **never** the hash; verify hit + miss; verify touches `last_used_at` and the touch is throttled; delete returns True/False.
- `current_principal`: a valid managed key authenticates (principal `key:<label>`); a revoked key is denied; managed keys work when env `api_key` is unset.
- Router: `POST` returns the token exactly once (201) and a second GET never exposes it; `GET` list shape carries no secret; `DELETE` 204/404; endpoints require a principal (401 without one).
- Frontend: light vitest for the once-shown-token flow / list render.

## Out of scope (later issues)

Per-key scopes/permissions, key expiry/rotation, per-key rate limits. Brute-force throttle is Phase C (#260).
