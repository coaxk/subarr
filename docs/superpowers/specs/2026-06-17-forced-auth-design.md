# Forced authentication — design (#238)

**Date:** 2026-06-17
**Issue:** #238 (supersedes the #238-A interim no-auth banner)
**Status:** approved (brainstorm), Phase A targeted for implementation

## Goal

subarr ships unauthenticated by default: any host that can reach the port drives
the full API (mutates Sonarr/Radarr, restarts subgen, edits library roots, reads
paths). The arr-suite forced authentication in 2023 after no-auth-by-default
became a known compromise vector. This makes subarr **require authentication by
default**, matching that posture — **unless the operator explicitly delegates
auth to a reverse proxy.**

Hard requirement throughout: **never improperly lock a user out.** Every
enforcement mechanism has an independent, documented escape hatch.

## Model

A request is authorized if it presents **any** valid principal:
1. a valid **session cookie** (forms login), or
2. **env basic auth** (`SUBARR_USER`+`SUBARR_PASS`) — automation + recovery, or
3. a valid **API key** (`X-Api-Key`) — the env `SUBARR_API_KEY` (Phase A) and
   user-generated managed keys (Phase B).

If **no principal is configured AND no stored credential exists** → **first-run
SETUP mode** (create the admin account).

**Delegated-auth opt-out:** `SUBARR_AUTH_DISABLED=1` turns built-in auth fully
off (the operator's reverse proxy — Authelia / Caddy / Traefik, which the README
already recommends — owns auth). This skips the setup gate AND suppresses the
no-auth banner: intentional delegation is not exposure.

## Phases (one design, phased delivery — each its own PR)

- **Phase A — Core forms-auth (this session).** setup/login/logout, session
  cookie, pbkdf2 credential, the three recovery paths, `SUBARR_AUTH_DISABLED`,
  explanatory UX, web-security hardening. The security win.
- **Phase B — User-managed API keys.** Generate high-entropy keys, store HASHED
  + last-4 hint, mask in the UI, revoke; constant-time check; middleware accepts
  them alongside session/env. Filed as a follow-up issue.
- **Phase C — Brute-force throttle.** In-memory IP sliding-window on login;
  no permanent account lockout; trusted-proxy-aware client IP. Follow-up issue.
- **Phase D — stack-trace-exposure cleanup** (the ~19 `py/stack-trace-exposure`
  findings). Follow-up issue; its risk drops once auth is enforced.

---

## Phase A — detailed design

### Components

- **`auth_store` (new, SQLite, single row + secret):**
  - the admin credential: `username`, pbkdf2 `password_hash`, `salt`, `iterations`.
  - the **session secret** (random, generated once, persisted → sessions survive
    restart; rotating it logs everyone out, which is also the manual "kill all
    sessions" lever).
  - Single-account model (matches arr; multi-user is YAGNI).
  - **Init + concurrency (review #3):** the table is created by a **migration**
    that runs in the lifespan startup hook, *before* uvicorn accepts
    connections — so the burst of tab requests at boot never races to
    initialize. The credential row is keyed at a fixed `id=1` and setup uses an
    **atomic insert-if-absent** (`INSERT … WHERE NOT EXISTS` / `INSERT OR
    IGNORE`), so two concurrent `POST /setup` calls can't double-create.
- **`auth.py` (rework):**
  - `hash_password` / `verify_password`: `hashlib.pbkdf2_hmac('sha256', …)`,
    per-cred random salt, ~200k iterations, `hmac.compare_digest` verify.
  - `current_principal(request)`: resolve session / env-basic / api-key → a
    principal or None. Used by the middleware. **Basic-header fall-through
    (review #1):** a present `Authorization: Basic` header is only treated as a
    subarr principal when its username equals `SUBARR_USER` AND the password
    verifies; any other Basic header (e.g. a reverse proxy passing a
    domain-level credential downstream) is **ignored — never a 401**. Returning
    None falls through to the next principal; only the gate decides the 401.
- **`SessionMiddleware`** (Starlette built-in; adds the `itsdangerous` dep,
  pinned): signed session cookie, `secret_key` from `auth_store`. Cookie is
  **HttpOnly**, **SameSite=Lax**, `https_only=False` (plain-http LAN works;
  TLS-terminating-proxy users are covered by their proxy — forwarded-proto
  Secure handling is a documented follow-up). **Session is rotated on login**
  (anti session-fixation). **SameSite knob (review #5):** `SUBARR_COOKIE_SAMESITE`
  overrides the `Lax` default; `none` (subarr embedded in a cross-site dashboard
  iframe — Organizr/Homer) forces `https_only=True`, since browsers reject
  `SameSite=None` without `Secure`. Thin pass-through to SessionMiddleware.
- **`AuthGateMiddleware` (new):** the enforcement gate. If `SUBARR_AUTH_DISABLED`
  → pass through. Else: bypass paths (below) pass; otherwise require a principal,
  else 401 (JSON for `/api/*`) or redirect to `/login` (HTML). Replaces the
  optional-only `BasicAuthMiddleware` (its checks fold into `current_principal`).
- **`routers/auth.py`:**
  - `GET  /api/auth/state` — `{needs_setup, authed, username?}`. Bypassed.
    **needs_setup is env-aware (review #4):** `needs_setup = (no stored cred)
    AND (no env SUBARR_USER) AND (no env SUBARR_API_KEY)`. Any env credential ⇒
    `needs_setup=false`, even with a blank SQLite row — so an existing
    api-key/basic install upgrading to this version is NOT forced into setup and
    cron/scripts hitting the API keep working. The gate enforces the same rule.
  - `POST /api/auth/setup` — create the admin cred; **allowed only when
    needs_setup** (409 otherwise). Auto-logs-in (sets session).
  - `POST /api/auth/login` — verify (stored or env) → rotate+set session. Generic
    error on failure.
  - `POST /api/auth/logout` — clear session.
- **CLI (`python -m subarr.cli`):** `reset-auth` (clear stored cred → setup) and
  `set-password` (set the admin password directly). For `docker exec` recovery.
- **Frontend:** an auth entry that branches on `/api/auth/state`:
  - **setup** form (first run) — leads with the *why* (security hardening,
    matching Sonarr/Radarr) + links the recovery doc.
  - **login** form — username/password, generic error.
  - **logout** control in the chrome.
  - **Global 401 interceptor (review #2):** a shared fetch wrapper all API calls
    route through; on `401` it wipes local auth state and redirects to
    `/login?next=<current>`. Without it a session that expires mid-session (or a
    restart that invalidates the cookie) makes background polls 401 and the UI
    spins/errors silently instead of bouncing to login. (subarr's fetches are
    currently scattered — Phase A introduces the one wrapper and routes calls
    through it.)

### Flows

- **Fresh boot:** state→`needs_setup` → setup form → `POST /setup` (create cred,
  auto-login) → app.
- **Returning:** state→unauthed → login → session → app. **Logout** → clear → login.
- **Upgrade (existing no-auth install):** enters SETUP on next load. The setup
  screen explains *why* this appeared; CHANGELOG + a one-time startup log line
  also announce it. Existing env-basic / api-key installs are unaffected.

### Enforcement / bypass

No-auth paths: `/api/health`, `/static/`, `/login`, `/api/auth/state`,
`/api/auth/login`, and `/api/auth/setup` (only while `needs_setup`). Everything
else requires a principal. `SUBARR_AUTH_DISABLED=1` bypasses the gate entirely.

### Recovery — anti-lockout guarantees (your hard requirement)

Three **independent** paths, none requiring DB surgery:
1. **Env override** — `SUBARR_USER`/`SUBARR_PASS` always accepted at login (edit
   compose, restart).
2. **Reset flag** — `SUBARR_AUTH_RESET=1` clears the stored cred on boot → setup.
3. **CLI** — `docker exec subarr python -m subarr.cli reset-auth` / `set-password`.

All three are documented on the setup screen and in the README. The setup
endpoint is reachable (no principal) whenever `needs_setup`, so a reset always
lands somewhere usable.

### Web-security hardening

- XSS: React-escaped; no `innerHTML`/`dangerouslySetInnerHTML` on user fields;
  HttpOnly cookie.
- CSRF: existing `CsrfOriginMiddleware` (origin check) + SameSite=Lax.
- Session fixation: rotate on login.
- User enumeration: generic "invalid username or password".
- Clickjacking: `security_headers.py` headers on the auth pages.
- Timing: `hmac.compare_digest` / `secrets.compare_digest`.
- Open redirect: if `?next=` is honored, accept local-path only.
- No credentials/secrets in logs.

### Testing (Phase A)

- `auth_store`: hash round-trip, verify ok/wrong, salt uniqueness, secret persists.
- session: rotate-on-login; logout clears; tampered/expired cookie rejected.
- `current_principal`: session / env-basic / api-key each accepted; none → None.
- `AuthGateMiddleware`: bypass paths; principal accepted; none → 401 (api) /
  redirect (html); setup-mode gating; `SUBARR_AUTH_DISABLED` passes through.
- endpoints: setup only when needs_setup (409 after); login ok/fail (generic
  error); logout; state shape.
- recovery: env override accepted; `SUBARR_AUTH_RESET` clears; CLI reset/set.
- migration: env-basic/api-key install NOT forced into setup.
- enumeration: same response/shape for unknown-user vs wrong-password.
- review #1: a Basic header with a non-matching username falls through (no 401)
  and a valid session on the same request is still honored.
- review #4: `/api/auth/state` returns `needs_setup=false` when `SUBARR_API_KEY`
  (or `SUBARR_USER`) is set even with an empty cred row; true only when all
  three are absent.
- review #3: concurrent `POST /setup` creates exactly one credential (atomic).
- review #5: `SUBARR_COOKIE_SAMESITE=none` ⇒ cookie SameSite=None + Secure.
- review #2 (frontend): the fetch wrapper redirects to `/login?next=` on 401.

## Out of scope (Phase A)

Multi-user/roles; OAuth/OIDC; password-strength policy beyond a minimum length;
the managed API keys (B), brute-force throttle (C), and stack-trace cleanup (D).
