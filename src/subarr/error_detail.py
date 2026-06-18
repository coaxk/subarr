"""#261: leak-free error messages for HTTP responses.

CodeQL `py/stack-trace-exposure` flags any exception text (`str(e)`) reaching an
HTTP response — an exception message can carry internal paths, hostnames, or
stack detail. `safe_error` returns a **constant** message chosen by exception
TYPE / HTTP status, never derived from the exception's own text, so the operator
still learns the *kind* of failure ("authentication failed", "could not
connect") while the full detail goes only to the server log.

Usage at a call site:
    except SomeError as e:
        log.warning("widget fetch failed: %s", e)   # full detail, server-side
        raise HTTPException(502, detail=safe_error(e))   # safe, leak-free
"""

from __future__ import annotations


import logging

import httpx

from .docker_client import DockerUnavailable
from .integrations import IntegrationError
from .integrations.ollama import OllamaError
from .subgen_client import SubgenUnavailable

# Our own typed "service is down / misconfigured" errors. Their messages are
# author-written, but we still don't echo them — type-mapping keeps CodeQL clean
# and the wording consistent.
_SERVICE_ERRORS = (IntegrationError, OllamaError, SubgenUnavailable, DockerUnavailable)

log = logging.getLogger(__name__)


def safe_error(e: BaseException) -> str:
    """A constant, leak-free message describing the *kind* of failure. Never
    contains any text derived from the exception itself. Logs the real exception
    server-side so the detail isn't lost — call sites can just use the return
    value as the client-facing `detail`/`error` without adding their own log."""
    log.warning("handled %s (returning sanitized message): %s", type(e).__name__, e)
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code in (401, 403):
            return "authentication failed — check the API key or token"
        if code == 404:
            return "the requested resource was not found"
        if code == 429:
            return "the upstream service is rate-limiting requests"
        if 500 <= code <= 599:
            return "the upstream service returned a server error"
        return "the upstream service rejected the request"
    if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout)):
        return "could not connect — check the URL and that the service is running"
    if isinstance(e, httpx.TimeoutException):
        return "the request timed out"
    if isinstance(e, _SERVICE_ERRORS):
        return "the service is unavailable or misconfigured"
    return "an unexpected error occurred"
