"""HTTP Basic auth middleware.

Enabled when BOTH SUBARR_USER and SUBARR_PASS env vars are set. When
either is empty, the middleware is a no-op (no auth). This is the
in-product fallback for users who can't put subarr behind a reverse
proxy; we recommend Authelia / Caddy basicauth / Traefik forward-auth
for production.

Allowlist
---------
A small set of paths bypass auth for monitoring tooling:
  /api/health    — used by Docker healthchecks + Uptime Kuma etc.

The login page is implicit (browser's Basic-Auth dialog). No login
form, no cookies, no session — every request carries credentials.
Use a strong password.

Honest limitations
------------------
  - One global user/password. No per-user identity, no audit trail
    of "who did what".
  - Basic auth credentials transmitted on every request. HTTPS-only
    in any setup where this matters (use a reverse proxy that
    terminates TLS).
  - No rate-limiting. A reverse proxy with fail2ban handles that
    better than we should.

If you need any of the above, put subarr behind Authelia.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets


log = logging.getLogger(__name__)


# Allowlist — paths that always bypass auth. Keep tight: just the
# monitoring path. /static/* is allowlisted because the index.html
# already requires auth, so static assets need to be reachable to
# render the auth-required page itself.
_BYPASS_EXACT = {
    "/api/health",
}
_BYPASS_PREFIXES = ("/static/",)


class BasicAuthMiddleware:
    """ASGI middleware factory. Pass user + password at construct time.

    Designed for FastAPI's `app.add_middleware()` interface.
    """

    def __init__(self, app, *, user: str, password: str):
        self._app = app
        self._user = user
        self._password = password
        self._enabled = bool(user and password)
        if self._enabled:
            log.info("basic auth enabled (user=%s, password=<%d chars>)", user, len(password))
        else:
            log.warning(
                "basic auth DISABLED — the API is UNAUTHENTICATED. This is fine "
                "on a trusted LAN, but do NOT expose subarr to the internet "
                "without a reverse proxy / auth. Set SUBARR_USER + SUBARR_PASS "
                "to enable built-in basic auth."
            )

    async def __call__(self, scope, receive, send):
        # Only HTTP requests carry auth (websockets / lifespan pass-through).
        if scope["type"] != "http" or not self._enabled:
            await self._app(scope, receive, send)
            return

        path = scope.get("path", "/")
        if path in _BYPASS_EXACT or any(path.startswith(p) for p in _BYPASS_PREFIXES):
            await self._app(scope, receive, send)
            return

        if not self._check_auth(scope):
            await self._challenge(send)
            return

        await self._app(scope, receive, send)

    def _check_auth(self, scope) -> bool:
        headers = dict(scope.get("headers") or [])
        auth_hdr = headers.get(b"authorization", b"").decode("latin-1")
        if not auth_hdr.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(auth_hdr[len("Basic ") :]).decode("utf-8")
        except Exception:
            return False
        if ":" not in decoded:
            return False
        provided_user, _, provided_pass = decoded.partition(":")
        # secrets.compare_digest dodges timing attacks vs ==.
        user_ok = secrets.compare_digest(provided_user, self._user)
        pass_ok = secrets.compare_digest(provided_pass, self._password)
        return user_ok and pass_ok

    @staticmethod
    async def _challenge(send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"www-authenticate", b'Basic realm="subarr", charset="UTF-8"'),
                    (b"content-type", b"text/plain; charset=utf-8"),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"Authentication required.\n",
            }
        )


def is_path_bypassed(path: str) -> bool:
    """Public helper for tests + future code that needs to know whether
    a path skips auth."""
    if path in _BYPASS_EXACT:
        return True
    return any(path.startswith(p) for p in _BYPASS_PREFIXES)


# ─── #238 forced auth: hashing, principal resolution, the gate ──────────────

_PBKDF2_ITERS = 200_000


def hash_password(password: str, *, iterations: int = _PBKDF2_ITERS) -> tuple[str, str, int]:
    """Return (hash_hex, salt_hex, iterations). pbkdf2-hmac-sha256, fresh salt."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return dk.hex(), salt.hex(), iterations


def verify_password(password: str, password_hash: str, salt: str, iterations: int) -> bool:
    """Constant-time verify. False (never raises) on malformed stored values."""
    try:
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), password_hash)


def _decode_basic(auth_hdr: str) -> tuple[str, str] | None:
    if not auth_hdr.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(auth_hdr[len("Basic ") :]).decode("utf-8")
    except Exception:  # noqa: BLE001 - any decode failure → not a usable Basic header
        return None
    if ":" not in decoded:
        return None
    user, _, pw = decoded.partition(":")
    return user, pw


def _query_param(query_string: bytes, key: str) -> str:
    from urllib.parse import parse_qs

    vals = parse_qs(query_string.decode("latin-1")).get(key)
    return vals[0] if vals else ""


def current_principal(scope, *, settings, store=None) -> str | None:
    """Resolve the request's principal, or None. Order: session cookie →
    env basic (ONLY when the username matches SUBARR_USER — a non-matching
    Basic header, e.g. a reverse proxy passing a domain credential downstream,
    falls through and is NEVER rejected here) → env API key → managed API key
    (#259, when a store is provided). Returning None lets the gate decide the
    401/redirect; this function never denies."""
    session = scope.get("session") or {}
    if session.get("user"):
        return str(session["user"])

    headers = dict(scope.get("headers") or [])

    if settings.auth_user and settings.auth_pass:
        cred = _decode_basic(headers.get(b"authorization", b"").decode("latin-1"))
        if cred is not None:
            user, pw = cred
            if secrets.compare_digest(user, settings.auth_user) and secrets.compare_digest(
                pw, settings.auth_pass
            ):
                return user
            # username/pass mismatch → fall through (do NOT reject here)

    # The presented API key (env or managed) arrives the same way.
    key = headers.get(b"x-api-key", b"").decode("latin-1") or _query_param(
        scope.get("query_string", b""), "apikey"
    )
    if key:
        if settings.api_key and secrets.compare_digest(key, settings.api_key):
            return "api-key"
        if store is not None:
            row = store.verify_api_key(key)
            if row is not None:
                return f"key:{row['label']}"

    return None


# #292: pre-computed dummy hash used to equalize verify_login timing on the
# unhappy path (unknown username / no stored credential). Both the known-user
# and unknown-user branches call verify_password, so an attacker cannot
# distinguish "no such user" (~µs) from "wrong password" (~400ms PBKDF2).
# Generated once at import time; the result is always discarded.
_DUMMY_HASH, _DUMMY_SALT, _DUMMY_ITERS = hash_password("dummy-timing-equalizer")


def verify_login(username: str, password: str, *, store, settings) -> bool:
    """True if the pair matches the **env override** (`SUBARR_USER`/`PASS` —
    the always-on recovery credential) or the **stored** credential. Constant-
    time throughout; generic on caller side (never reveal which check failed)."""
    if settings.auth_user and settings.auth_pass:
        if secrets.compare_digest(username, settings.auth_user) and secrets.compare_digest(
            password, settings.auth_pass
        ):
            return True
    cred = store.get_credential()
    if cred and secrets.compare_digest(username, cred["username"]):
        return verify_password(password, cred["password_hash"], cred["salt"], cred["iterations"])
    # Unknown username or no stored credential: run a dummy PBKDF2 verify so
    # the response time matches the known-user+wrong-password path, preventing
    # username enumeration via timing side-channel. The result is always False.
    verify_password(password, _DUMMY_HASH, _DUMMY_SALT, _DUMMY_ITERS)
    return False


def needs_setup(settings, store) -> bool:
    """First-run setup is needed only when NOTHING provides auth: no stored
    credential AND no env basic-user AND no env API key. Any env credential ⇒
    False, so existing api-key/basic installs are never forced into setup
    (their cron/scripts keep working through the upgrade)."""
    if settings.auth_user or settings.api_key:
        return False
    return not store.has_credential()


_GATE_BYPASS_EXACT = {
    "/api/health",
    "/login",
    "/setup",
    "/api/auth/state",
    "/api/auth/login",
    "/api/auth/logout",
}
_GATE_BYPASS_PREFIXES = ("/static/",)


class AuthGateMiddleware:
    """#238: require a principal on every non-bypassed route, unless auth is
    delegated (SUBARR_AUTH_DISABLED). In first-run setup mode, unauthenticated
    requests are steered to /setup; otherwise to /login (HTML) or 401 (/api/*)."""

    def __init__(self, app, *, settings, get_store):
        self._app = app
        self._settings = settings
        # Lazy: the AuthStore is built in the lifespan startup, AFTER this
        # middleware is registered at import. get_store() resolves it per request.
        self._get_store = get_store

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self._settings.auth_disabled:
            await self._app(scope, receive, send)
            return

        store = self._get_store()
        if store is None:
            # Pre-lifespan (store not built yet) never serves real traffic; fail
            # open rather than crash on the off chance a request arrives early.
            await self._app(scope, receive, send)
            return

        path = scope.get("path", "/")
        setup_mode = needs_setup(self._settings, store)
        if self._bypassed(path, setup_mode):
            await self._app(scope, receive, send)
            return

        if current_principal(scope, settings=self._settings, store=store) is not None:
            await self._app(scope, receive, send)
            return

        if setup_mode:
            await _redirect(send, "/setup")
        elif path.startswith("/api/"):
            await _deny_json(send)
        else:
            await _redirect(send, "/login")

    def _bypassed(self, path: str, setup_mode: bool) -> bool:
        if path in _GATE_BYPASS_EXACT or any(path.startswith(p) for p in _GATE_BYPASS_PREFIXES):
            return True
        # The setup endpoint is reachable WITHOUT auth only while setup is needed.
        return setup_mode and path == "/api/auth/setup"


async def _redirect(send, location: str) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 302,
            "headers": [(b"location", location.encode("latin-1"))],
        }
    )
    await send({"type": "http.response.body", "body": b""})


async def _deny_json(send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": b'{"detail": "authentication required"}'})
