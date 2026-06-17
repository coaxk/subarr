# Forced Auth — Phase A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline). TDD, checkbox steps, frequent commits. Spec: `docs/superpowers/specs/2026-06-17-forced-auth-design.md`.

**Goal:** Make subarr require authentication by default (forms login + session), with three independent recovery paths and a proxy-delegation opt-out — without breaking proxied or automated installs.

**Architecture:** A single-admin credential + session secret in SQLite (`auth_store`); pbkdf2 password hashing (stdlib); Starlette `SessionMiddleware` (adds `itsdangerous`) for signed session cookies; a `current_principal` resolver (session / env-basic-with-fall-through / api-key) feeding an `AuthGateMiddleware` that gates all non-bypassed routes unless `SUBARR_AUTH_DISABLED`. Forms UI for setup/login/logout; a global fetch wrapper redirects to login on 401.

**Tech Stack:** FastAPI/Starlette, sqlite3, hashlib/hmac/secrets (stdlib), itsdangerous, React (esbuild IIFE), vitest/pytest.

---

## File structure

- Create `src/subarr/auth_store.py` — credential + session-secret storage (atomic single-row).
- Create `src/subarr/migrations/021_auth.sql` — `auth_credential`, `auth_secret` tables.
- Modify `src/subarr/auth.py` — pbkdf2 hash/verify, `current_principal`, `AuthGateMiddleware`, `needs_setup`. (Reworks the old optional `BasicAuthMiddleware`.)
- Create `src/subarr/routers/auth.py` — `/api/auth/{state,setup,login,logout}`.
- Create `src/subarr/cli.py` — `reset-auth`, `set-password`.
- Modify `src/subarr/config.py` — `auth_disabled`, `auth_reset`, `cookie_samesite` (+ existing user/pass/api_key).
- Modify `src/subarr/app.py` — SessionMiddleware + AuthGateMiddleware + auth router + boot migration + reset-flag handling + auth_store on state.
- Create `src/subarr/static/v1/home-hifi/auth.jsx` + `entries/auth.entry.jsx` — setup/login page.
- Create `src/subarr/static/v1/home-hifi/api.jsx` — `apiFetch` wrapper + global 401 → `/login?next=`.
- Modify `chrome.jsx` — logout control; route existing fetches through `apiFetch` (incremental).
- Modify `scripts/build-frontend.mjs` — add `auth` page; serve `/login` + `/setup`.
- Modify `README.md` + `CHANGELOG.md` — recovery, proxy, env table.
- Tests: `tests/test_auth_store.py`, `tests/test_auth.py`, `tests/test_auth_router.py`, `tests/test_auth_cli.py`, `tests/test_auth_recovery.py`, vitest for `api.jsx`.

---

## Task 1: config flags

**Files:** Modify `src/subarr/config.py`; Test `tests/test_config_auth.py`

- [ ] Step 1: failing test — `settings` exposes `auth_disabled: bool`, `auth_reset: bool`, `cookie_samesite: str` ("lax" default), reading `SUBARR_AUTH_DISABLED`/`SUBARR_AUTH_RESET` (bool coerce) + `SUBARR_COOKIE_SAMESITE` (lower, default "lax"). Assert defaults + env overrides via `subarr_env`/monkeypatch.
- [ ] Step 2: run → fail (AttributeError).
- [ ] Step 3: add the three fields to the Settings dataclass + `load()` (mirror existing `_env_or` + `_coerce_bool` patterns; `cookie_samesite` validated to one of lax/strict/none, else lax).
- [ ] Step 4: run → pass.
- [ ] Step 5: commit `feat(auth): config flags (auth_disabled/reset/cookie_samesite)`.

## Task 2: auth_store + migration

**Files:** Create `src/subarr/migrations/021_auth.sql`, `src/subarr/auth_store.py`; Test `tests/test_auth_store.py`

- [ ] Step 1: failing tests — migration creates `auth_credential` (id PK=1, username, password_hash, salt, iterations, created_at) + `auth_secret` (id PK=1, secret, created_at). `AuthStore(db)`:
  - `get_or_create_secret()` → stable across calls (persisted, same value second call).
  - `has_credential()` False initially; `set_credential(user, hash, salt, iters)` then True; `get_credential()` returns the row.
  - `set_credential` is atomic single-row: calling twice REPLACES (id=1) — last write wins; `create_credential_if_absent(...)` returns False when one already exists (for setup race).
  - `clear_credential()` → has_credential() False.
- [ ] Step 2: run → fail.
- [ ] Step 3: write the migration (CREATE TABLE IF NOT EXISTS … ) + `AuthStore` (WAL, lock around conn, fixed id=1, `INSERT … ON CONFLICT(id) DO UPDATE` for set; `INSERT OR IGNORE` + rowcount for create-if-absent; secret generated via `secrets.token_urlsafe(48)`).
- [ ] Step 4: run → pass.
- [ ] Step 5: commit `feat(auth): auth_store + migration 021 (credential + session secret)`.

## Task 3: password hashing

**Files:** Modify `src/subarr/auth.py`; Test `tests/test_auth.py`

- [ ] Step 1: failing tests — `hash_password(pw)` → `(hash, salt, iters)`; `verify_password(pw, hash, salt, iters)` True for correct, False for wrong; salt differs across calls; uses `hmac.compare_digest` (verify constant-time — assert behavior, and that a wrong pw of equal length still returns False).
- [ ] Step 2: run → fail.
- [ ] Step 3: implement with `hashlib.pbkdf2_hmac('sha256', pw.encode(), salt, iters)`, `salt = secrets.token_bytes(16)`, `iters = 200_000`, hex-encode, `hmac.compare_digest`.
- [ ] Step 4: run → pass.
- [ ] Step 5: commit `feat(auth): pbkdf2 password hashing`.

## Task 4: current_principal (with Basic fall-through)

**Files:** Modify `src/subarr/auth.py`; Test `tests/test_auth.py`

- [ ] Step 1: failing tests (build a fake request scope w/ headers + a session dict + a small fake AuthStore/env):
  - valid session (`scope["session"] == {"user": "admin"}`) → principal "admin".
  - env basic matching `SUBARR_USER`/`PASS` → principal.
  - **Basic header with a NON-matching username → None (fall-through), NOT an exception/401.**
  - api-key header matching env `SUBARR_API_KEY` → principal.
  - nothing → None.
- [ ] Step 2: run → fail.
- [ ] Step 3: implement `current_principal(scope, *, settings, store)`: check `scope.get("session", {}).get("user")`; else parse `authorization: Basic` → decode → only accept if `user == settings.auth_user and verify`; else parse `x-api-key`/`?apikey=` against `settings.api_key` (compare_digest) and Phase-B-managed keys (later). Return str principal or None. Pure (no I/O beyond store reads).
- [ ] Step 4: run → pass.
- [ ] Step 5: commit `feat(auth): current_principal resolver with Basic fall-through`.

## Task 5: needs_setup + AuthGateMiddleware

**Files:** Modify `src/subarr/auth.py`; Test `tests/test_auth.py`

- [ ] Step 1: failing tests:
  - `needs_setup(settings, store)` → True only when no stored cred AND no `SUBARR_USER` AND no `SUBARR_API_KEY`; False if any present (review #4).
  - `AuthGateMiddleware` (ASGI): `SUBARR_AUTH_DISABLED` → always pass. Bypass paths (`/api/health`, `/static/`, `/login`, `/setup`, `/api/auth/state`, `/api/auth/login`, and `/api/auth/setup` only when needs_setup) → pass. With a principal → pass. No principal + `/api/*` → 401 JSON. No principal + HTML → 302 `/login`. In setup mode, non-bypassed → 302 `/setup`.
- [ ] Step 2: run → fail.
- [ ] Step 3: implement the middleware (ASGI `__call__`, reuse `is_path_bypassed` style; call `current_principal`; branch on Accept/`/api/` for 401-vs-redirect).
- [ ] Step 4: run → pass.
- [ ] Step 5: commit `feat(auth): needs_setup + AuthGateMiddleware`.

## Task 6: auth router

**Files:** Create `src/subarr/routers/auth.py`; Test `tests/test_auth_router.py`

- [ ] Step 1: failing tests (TestClient with SessionMiddleware + a tmp AuthStore on app.state):
  - `GET /api/auth/state` → `{needs_setup: true, authed: false}` fresh; after setup → `needs_setup:false, authed:true`.
  - `POST /api/auth/setup {username,password}` when needs_setup → 201 + sets session (state authed). Second call → 409.
  - `POST /api/auth/login` wrong → 401 generic `{"detail":"invalid username or password"}`; right → 200 + session; **session rotated** (new cookie value).
  - `POST /api/auth/logout` → clears session (state authed:false).
  - `state` with env `SUBARR_API_KEY` set + empty cred → `needs_setup:false`.
- [ ] Step 2: run → fail.
- [ ] Step 3: implement router; setup uses `store.create_credential_if_absent`; login verifies stored OR env; on success `request.session.clear(); request.session["user"]=…` (rotate).
- [ ] Step 4: run → pass.
- [ ] Step 5: commit `feat(auth): setup/login/logout/state endpoints`.

## Task 7: app wiring

**Files:** Modify `src/subarr/app.py`, `pyproject.toml` (add `itsdangerous`); Test `tests/test_auth_recovery.py` + existing `tests/test_smoke.py`

- [ ] Step 1: failing tests — boot the app (smoke client): `/api/auth/state` reachable unauthenticated; a protected route (`/api/mode`) without auth → 401/redirect; with `SUBARR_AUTH_DISABLED=1` → protected route 200. `SUBARR_AUTH_RESET=1` env → after boot, `needs_setup` true even if a cred existed.
- [ ] Step 2: run → fail.
- [ ] Step 3: add `itsdangerous` to pyproject deps; in app lifespan: run migrations (021 included), construct `AuthStore`, on `settings.auth_reset` call `clear_credential()` + log; add `SessionMiddleware(secret_key=store.get_or_create_secret(), same_site=settings.cookie_samesite, https_only=(cookie_samesite=='none'), http_only=True)`; add `AuthGateMiddleware`; `app.include_router(auth.router)`. Order middlewares so Session runs before the gate.
- [ ] Step 4: run → pass; run full suite (gate must not break existing tests — they use `subarr_env`; ensure tests set a principal or `SUBARR_AUTH_DISABLED`). **NOTE:** existing tests will 401 under the gate — add `SUBARR_AUTH_DISABLED=1` to the `subarr_env` fixture (tests exercise app logic, not auth) and let the dedicated auth tests cover enforcement.
- [ ] Step 5: commit `feat(auth): wire SessionMiddleware + gate + reset flag`.

## Task 8: CLI recovery

**Files:** Create `src/subarr/cli.py`; Test `tests/test_auth_cli.py`

- [ ] Step 1: failing tests — `cli.main(["reset-auth"])` clears the cred (has_credential False after); `cli.main(["set-password","--username","admin","--password","x"])` sets a verifiable cred. Both operate on `settings.db_path` AuthStore, print a confirmation, return 0.
- [ ] Step 2: run → fail.
- [ ] Step 3: implement argparse CLI (`reset-auth`, `set-password`), `if __name__=='__main__'`. Hashes via Task 3.
- [ ] Step 4: run → pass.
- [ ] Step 5: commit `feat(auth): cli reset-auth / set-password`.

## Task 9: frontend apiFetch + 401 interceptor

**Files:** Create `src/subarr/static/v1/home-hifi/api.jsx`; Test `src/subarr/static/v1/home-hifi/__tests__/api.test.js`

- [ ] Step 1: failing vitest — `apiFetch(url, opts)` calls global `fetch`; on `{status:401}` it sets `window.location` to `/login?next=<encoded current path>` and the returned promise rejects/sentinels; on 200 returns the response; non-401 errors pass through. Mock `fetch` + a `window.location` stub.
- [ ] Step 2: run → fail.
- [ ] Step 3: implement `apiFetch` (wraps fetch with `credentials:'same-origin'`; if `r.status===401` → redirect + throw a tagged error).
- [ ] Step 4: run → pass (`npm run test:frontend`).
- [ ] Step 5: commit `feat(auth): apiFetch wrapper with global 401 redirect`.

## Task 10: auth page (setup/login) + routing + build

**Files:** Create `auth.jsx`, `entries/auth.entry.jsx`; Modify `scripts/build-frontend.mjs`, the FastAPI `/login`+`/setup` HTML routes (app.py)

- [ ] Step 1: (component — manual/visual; logic covered by api.test + router tests). Build `AuthPage`: fetches `/api/auth/state`; renders **setup** form (with the *why* copy + recovery doc link) when `needs_setup`, else **login** form; submits via `apiFetch` to `/api/auth/setup`|`/login`; on success `window.location='/'`. Generic error display.
- [ ] Step 2: add `auth` to `build-frontend.mjs` PAGES; serve the auth bundle at `/login` and `/setup` (static HTML shell, like other pages).
- [ ] Step 3: `npm run build:frontend`; confirm `auth.bundle.js` builds; restore stray maps.
- [ ] Step 4: commit `feat(auth): setup/login page + routing`.

## Task 11: logout control

**Files:** Modify `src/subarr/static/v1/home-hifi/chrome.jsx`

- [ ] Step 1: add a logout control (in the top bar / user menu) → `apiFetch('/api/auth/logout',{method:'POST'})` then `window.location='/login'`. Hidden when `SUBARR_AUTH_DISABLED` (state has no session). 
- [ ] Step 2: `npm run build:frontend`; vitest green.
- [ ] Step 3: commit `feat(auth): logout control in chrome`.

## Task 12: docs

**Files:** Modify `README.md`, `CHANGELOG.md`

- [ ] Step 1: README — a "Authentication" section: forced setup, the 3 recovery paths (env override, `SUBARR_AUTH_RESET=1`, `docker exec … python -m subarr.cli reset-auth`), `SUBARR_AUTH_DISABLED` for proxy users, `SUBARR_COOKIE_SAMESITE` for iframe dashboards. CHANGELOG entry under Unreleased explaining the upgrade-to-setup behavior + recovery.
- [ ] Step 2: commit `docs(auth): authentication + recovery + proxy delegation`.

## Task 13: full verification

- [ ] Step 1: `PYTHONPATH=src pytest -q` → all green (incl. the `subarr_env` AUTH_DISABLED adjustment).
- [ ] Step 2: `npm run test:frontend` + `npm run build:frontend` (no drift).
- [ ] Step 3: manual smoke on a throwaway clean-DB container: fresh → setup → app; logout → login; `SUBARR_AUTH_RESET=1` → setup; `SUBARR_AUTH_DISABLED=1` → no gate; env `SUBARR_API_KEY` → no forced setup.
- [ ] Step 4: PR; CI green (CodeQL especially — new auth code); merge `--admin` only when fully complete.

## Notes / decisions

- **Existing tests under the gate:** the cleanest fix is `SUBARR_AUTH_DISABLED=1` in the shared `subarr_env` fixture — existing tests exercise app logic, not auth; the auth gate gets its own dedicated tests. (Alternative: inject a session — heavier.) Confirm during Task 7.
- **No new heavy deps:** only `itsdangerous` (Pallets, ubiquitous, pinned). pbkdf2 stdlib. No fastapi-users.
- **Managed API keys / brute-force throttle / stack-trace cleanup are Phases B/C/D** — filed separately.
