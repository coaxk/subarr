# Phase C — login brute-force throttle (#260)

**Goal:** slow credential-stuffing / brute-force against the login endpoint without ever permanently locking out a legitimate user (the explicit anti-footgun requirement from the #238 brainstorm).

**Context:** Phase C of the forced-auth roadmap (after #238/#258 forced auth, #259 managed keys, #265 session UX). In-memory + per-process — fine for subarr's single-container model.

---

## Two distinct IP controls (locked decisions)

These are **complementary**, not the same thing:

1. **Trusted proxies** (`SUBARR_TRUSTED_PROXIES`, CIDRs, default empty) — which upstream hops may set `X-Forwarded-For`, so the throttle keys on the **real client IP** behind a reverse proxy instead of the proxy's own IP. Correctness, not exemption.
2. **Never-throttle allowlist** (`SUBARR_LOGIN_ALLOWLIST`, CIDRs, default empty) — client IPs/ranges that **skip the throttle entirely** (your LAN, your automation box). Exemption.

Both are env vars set in the compose; both default empty (safe: no XFF trust, no exemptions). Surfaced **read-only** in Settings + documented in README. **No onboarding step** (env-only settings the wizard can't persist).

## Policy (sliding window, no permanent lockout)

- Track **failed** login attempts per resolved-client-IP in memory (deque of timestamps).
- On each attempt: drop timestamps older than the window. If the IP is allowlisted → never blocked. Else if remaining failures ≥ **max attempts** → return **429** with a `Retry-After` (seconds until the oldest failure ages out) — *without* checking the password.
- On a **failed** verify → append a timestamp. On **success** → clear that IP's deque.
- The window slides; after a quiet window the IP fully resets. **Never a permanent lock** — worst case is a short wait.
- **Defaults:** `SUBARR_LOGIN_MAX_ATTEMPTS=5`, `SUBARR_LOGIN_WINDOW_S=300` (max ~5-min wait).
- **Memory-DoS guard:** cap the IP map (e.g. 4096 entries); evict the oldest when full so a flood of distinct IPs can't grow it unbounded.

Applies to `POST /api/auth/login` and `POST /api/auth/setup` (setup is one-time but worth covering during the open setup window). Response is generic (no user enumeration).

## Client-IP resolution

`resolve_client_ip(peer_ip, xff_header, trusted_proxies) -> str`:
- If `peer_ip` is **not** in `trusted_proxies` → return `peer_ip`. `X-Forwarded-For` is ignored (a spoofed XFF from a direct attacker can neither evade the limit nor frame another IP).
- If `peer_ip` **is** trusted → walk the XFF list right-to-left, skipping entries that are themselves trusted; the first non-trusted entry is the real client. If all are trusted (or XFF empty), fall back to `peer_ip`.
- Malformed IPs are skipped defensively.

## Code units

- **`src/subarr/login_throttle.py`** (new):
  - `parse_cidrs(csv) -> list[ip_network]` — parse the env CSV once (stdlib `ipaddress`).
  - `ip_in(ip, networks) -> bool`.
  - `resolve_client_ip(peer, xff, trusted) -> str` (pure).
  - `LoginThrottle(max_attempts, window_s, allowlist, max_ips)`:
    - `check(ip, *, now) -> (blocked: bool, retry_after: int)`
    - `record_failure(ip, *, now)`
    - `clear(ip)`
    - allowlisted IPs short-circuit to `(False, 0)`; memory cap enforced on insert.
- **`config.py`**: `trusted_proxies: str`, `login_allowlist: str`, `login_max_attempts: int`, `login_window_s: int` (with the matching env reads).
- **`app.py` lifespan**: build `app_.state.login_throttle = LoginThrottle(...)` from settings.
- **`routers/auth.py`**: in `login` + `setup`, resolve the client IP, consult the throttle (429 + `Retry-After` when blocked), record failures, clear on success. A tiny `_client_ip(request, settings)` helper reads `request.client.host` + the `x-forwarded-for` header.
- **Read-only surface**: extend an auth-status/admin endpoint (or a small `GET /api/auth/throttle-config`) returning effective `{max_attempts, window_s, trusted_proxies: [...], allowlist: [...]}` (no secrets) + a read-only Settings card.

## Error handling & edges

- Throttle/IP-resolution failures must never block a legitimate login — wrap defensively; on any internal error, fail **open** (allow the attempt) and log.
- `Retry-After` is best-effort seconds; the client just needs "try later."
- Allowlist + trusted-proxies parsed once at startup; bad CIDR entries are logged and skipped, not fatal.

## Testing

- `resolve_client_ip`: direct (no proxy) → peer; single trusted proxy → XFF client; multi-proxy chain → first untrusted; spoofed XFF from untrusted peer → ignored (peer used); malformed XFF entries skipped.
- `LoginThrottle`: under threshold allowed; at threshold blocked with sane `retry_after`; window slide resets; success clears; allowlisted IP never blocked even past threshold; memory cap evicts.
- Routes: N failed logins from one IP → 429 with `Retry-After`; an allowlisted IP never 429s; a correct password still works mid-throttle for a *different* IP; setup covered.
- Read-only config endpoint shape (no secrets).

## Out of scope

Distributed/persistent rate limiting (multi-replica), CAPTCHA, account-level lockout (we lock by IP-window, never the account).
