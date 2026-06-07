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
import logging
import secrets
from typing import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import Response as FastAPIResponse

log = logging.getLogger(__name__)


# Allowlist — paths that always bypass auth. Keep tight: just the
# monitoring path. /static/* is allowlisted because the index.html
# already requires auth, so static assets need to be reachable to
# render the auth-required page itself.
_BYPASS_EXACT = {
    "/api/health",
}
_BYPASS_PREFIXES = (
    "/static/",
)


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
            log.info("basic auth enabled (user=%s, password=<%d chars>)",
                     user, len(password))
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
            decoded = base64.b64decode(auth_hdr[len("Basic "):]).decode("utf-8")
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
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"www-authenticate", b'Basic realm="subarr", charset="UTF-8"'),
                (b"content-type", b"text/plain; charset=utf-8"),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": b"Authentication required.\n",
        })


def is_path_bypassed(path: str) -> bool:
    """Public helper for tests + future code that needs to know whether
    a path skips auth."""
    if path in _BYPASS_EXACT:
        return True
    return any(path.startswith(p) for p in _BYPASS_PREFIXES)
