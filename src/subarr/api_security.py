"""#198 — API key (opt-in) + CSRF origin gate (default on) for /api/*.

Subarr's API mutates Sonarr (episodeFile PUT), triggers Bazarr tasks, edits
library roots, and can restart subgen via the docker socket. Two layers
close the browser-borne attack paths:

CsrfOriginMiddleware (ships ON for everyone, SUBARR_CSRF_PROTECTION=0 to
opt out): unsafe methods (POST/PUT/PATCH/DELETE) on /api/* must look
same-origin when the request carries browser fetch metadata.
  - `Sec-Fetch-Site` present (every modern browser): only `same-origin`
    passes. This survives reverse proxies that rewrite Host, because the
    BROWSER computed the relationship.
  - else `Origin` present (older browsers): its host must match the
    request's Host (or X-Forwarded-Host for proxied setups).
  - neither header: not a browser → pass. curl/httpx/the subgen webhook
    are not CSRF vectors; CSRF is specifically a browser ambient-authority
    attack.

ApiKeyMiddleware (opt-in via SUBARR_API_KEY): when a key is configured,
every /api/* request needs `X-Api-Key: <key>` or `?apikey=<key>` (the arr
convention; the query form exists so subgen's WEBHOOK_URL_COMPLETED can
carry it). Exempt: /api/health (docker healthchecks, uptime monitors),
/api/ui-bootstrap (below), /static/*.

/api/ui-bootstrap: hands the key to the bundled frontend — gated to
requests whose `Sec-Fetch-Site` is exactly `same-origin`, i.e. scripts
running on pages we served. A cross-site page can neither pass the gate
nor read the response (CORS). Same trust posture as the *arr family:
the key protects against CSRF and off-LAN access; protecting against a
hostile user ON your LAN additionally needs basic auth / a reverse proxy.
"""

from __future__ import annotations

import logging
import secrets
from urllib.parse import parse_qs, urlsplit

log = logging.getLogger(__name__)

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Paths that bypass the API key. Keep tight.
_KEY_BYPASS_EXACT = {
    "/api/health",  # docker HEALTHCHECK + uptime monitors
    "/api/ui-bootstrap",  # same-origin-gated key handout (route enforces)
    "/api/auth-status",  # boolean "is auth configured" for the no-auth banner;
    # leaks nothing an unauthenticated /api/* probe wouldn't already reveal, and
    # MUST be readable in the no-auth case the banner targets.
}
_KEY_BYPASS_PREFIXES = ("/static/",)


def _headers(scope) -> dict[bytes, bytes]:
    return dict(scope.get("headers") or [])


async def _deny(send, status: int, body: str) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json; charset=utf-8")],
        }
    )
    await send({"type": "http.response.body", "body": body.encode("utf-8")})


class CsrfOriginMiddleware:
    """Reject cross-origin browser writes to /api/*. ASGI, BasicAuth-style."""

    def __init__(self, app, *, enabled: bool = True):
        self._app = app
        self._enabled = enabled
        if not enabled:
            log.warning("CSRF origin protection DISABLED (SUBARR_CSRF_PROTECTION=0)")

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or not self._enabled
            or scope.get("method", "GET").upper() not in _UNSAFE_METHODS
            or not scope.get("path", "/").startswith("/api/")
        ):
            await self._app(scope, receive, send)
            return

        headers = _headers(scope)
        sfs = headers.get(b"sec-fetch-site", b"").decode("latin-1").lower()
        if sfs:
            # The browser told us the relationship — trust it. `none` is a
            # direct navigation (address bar), which cannot be a fetch from
            # another site but also is never how our UI calls the API.
            if sfs in ("same-origin", "none"):
                await self._app(scope, receive, send)
                return
            log.warning("CSRF gate: blocked %s %s (Sec-Fetch-Site=%s)", scope["method"], scope["path"], sfs)
            await _deny(
                send,
                403,
                '{"detail": "cross-origin request blocked (CSRF protection; '
                'set SUBARR_CSRF_PROTECTION=0 to disable)"}',
            )
            return

        origin = headers.get(b"origin", b"").decode("latin-1")
        if origin:
            origin_host = urlsplit(origin).netloc
            own_hosts = {
                headers.get(b"host", b"").decode("latin-1"),
                headers.get(b"x-forwarded-host", b"").decode("latin-1"),
            }
            if origin_host and origin_host in own_hosts:
                await self._app(scope, receive, send)
                return
            log.warning("CSRF gate: blocked %s %s (Origin=%s)", scope["method"], scope["path"], origin)
            await _deny(
                send,
                403,
                '{"detail": "cross-origin request blocked (CSRF protection; '
                'set SUBARR_CSRF_PROTECTION=0 to disable)"}',
            )
            return

        # No browser fetch metadata at all → script/CLI client, not CSRF.
        await self._app(scope, receive, send)


class ApiKeyMiddleware:
    """Require X-Api-Key / ?apikey= on /api/* when a key is configured."""

    def __init__(self, app, *, api_key: str):
        self._app = app
        self._key = api_key or ""
        if self._key:
            log.info("API key auth enabled for /api/* (key=<%d chars>)", len(self._key))

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self._key:
            await self._app(scope, receive, send)
            return
        path = scope.get("path", "/")
        if not path.startswith("/api/"):
            await self._app(scope, receive, send)
            return
        if path in _KEY_BYPASS_EXACT or any(path.startswith(p) for p in _KEY_BYPASS_PREFIXES):
            await self._app(scope, receive, send)
            return

        provided = _headers(scope).get(b"x-api-key", b"").decode("latin-1")
        if not provided:
            qs = parse_qs(scope.get("query_string", b"").decode("latin-1"))
            provided = (qs.get("apikey") or [""])[0]
        if provided and secrets.compare_digest(provided, self._key):
            await self._app(scope, receive, send)
            return
        await _deny(send, 401, '{"detail": "API key required (X-Api-Key header or ?apikey=)"}')
