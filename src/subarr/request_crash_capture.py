"""#199 — feed unhandled request-handler exceptions to the crash store.

Crash telemetry (#157 P2) supervises the background loops; this closes the
other half: an exception that escapes a route handler (a 500) is recorded as
the same sanitized (exc_type, module:line) aggregate — visible locally in
the Settings "Crash reports (24h)" row and fleet-wide via crash_counts_24h.

Registered INNERMOST in the middleware stack so it only ever sees exceptions
that actually escaped the app: handled HTTPExceptions (404s, validation
errors) resolve inside the routing app and never reach it. The original
exception is always re-raised, so FastAPI/Starlette's normal 500 handling —
and its log line — are completely untouched.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class RequestCrashCaptureMiddleware:
    """ASGI wrapper: record-and-re-raise for unhandled handler exceptions."""

    def __init__(self, app, *, crash_recorder=None):
        self._app = app
        self._recorder = crash_recorder

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self._recorder is None:
            await self._app(scope, receive, send)
            return
        try:
            await self._app(scope, receive, send)
        except Exception as exc:
            # Best-effort: a broken recorder must never mask the real error.
            try:
                self._recorder(exc)
            except Exception:
                log.debug("request crash recorder failed", exc_info=True)
            raise
