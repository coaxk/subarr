"""#261: safe_error maps exceptions to constant, leak-free messages."""

from __future__ import annotations

import httpx

from subarr.docker_client import DockerUnavailable
from subarr.error_detail import safe_error
from subarr.integrations import IntegrationError
from subarr.integrations.ollama import OllamaError
from subarr.subgen_client import SubgenUnavailable


def _http_status(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "http://svc.test/api")
    resp = httpx.Response(code, request=req)
    return httpx.HTTPStatusError(f"HTTP {code}", request=req, response=resp)


def test_auth_status_codes():
    for code in (401, 403):
        assert "authentication" in safe_error(_http_status(code)).lower()


def test_not_found_rate_limited_server():
    assert "not found" in safe_error(_http_status(404)).lower()
    assert "rate" in safe_error(_http_status(429)).lower()
    assert "server error" in safe_error(_http_status(500)).lower()


def test_connect_and_timeout():
    assert "connect" in safe_error(httpx.ConnectError("boom")).lower()
    assert "timed out" in safe_error(httpx.TimeoutException("slow")).lower()


def test_typed_integration_errors_are_generic():
    for e in (
        OllamaError("model /secret/path failed"),
        IntegrationError("internal stack detail"),
        SubgenUnavailable("http://subgen:9000 refused at line 42"),
        DockerUnavailable("/var/run/docker.sock permission denied"),
    ):
        msg = safe_error(e)
        assert "unavailable" in msg.lower() or "misconfigured" in msg.lower()


def test_never_leaks_the_raw_message():
    secret = "/etc/subarr/secret.key line 99 Traceback"
    msg = safe_error(ValueError(secret))
    assert secret not in msg
    assert "unexpected" in msg.lower()


def test_returns_plain_string():
    assert isinstance(safe_error(Exception("x")), str)
