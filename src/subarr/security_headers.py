"""Security-header ASGI middleware (issue #138).

Sets a conservative, app-wide set of response headers on every HTTP
response (static assets included). Pure-ASGI (no Starlette
BaseHTTPMiddleware) so it adds no per-request task overhead and composes
cleanly with the GZip + BasicAuth middlewares.

CSP notes
---------
The v1 React UI is served as static bundles from /static/v1 and uses:
  - inline <style> blocks / style attributes -> style-src 'unsafe-inline'
  - Google Fonts CSS + font files            -> fonts.googleapis.com / fonts.gstatic.com
  - GitHub-hosted images (og-card etc)       -> img-src https://github.com
  - data: URIs for small inline images       -> img-src data:
Scripts are self-hosted (vendor/react*.js + *.bundle.js), so script-src
stays 'self' with NO 'unsafe-inline' / 'unsafe-eval'. React style={} props
compile to JS property assignments, not inline style attributes, so they
need no CSP relaxation. If a future screen needs an inline <script>, prefer
hashing it over loosening this.
"""

from __future__ import annotations

_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: https://github.com; "
    "connect-src 'self'; "
    "media-src 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none';"
)

_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"content-security-policy", _CSP.encode("latin-1")),
)


class SecurityHeadersMiddleware:
    """Pure-ASGI middleware that injects security headers on HTTP responses."""

    def __init__(self, app):
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def _send(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                existing = {k.lower() for k, _ in headers}
                for name, value in _HEADERS:
                    if name not in existing:
                        headers.append((name, value))
            await send(message)

        await self._app(scope, receive, _send)
