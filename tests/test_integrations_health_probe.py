"""_probe() bazarr_badges path: status + badges run concurrently, but BOTH
results must always be retrieved so a Bazarr blip never leaves a dangling task
('Task exception was never retrieved' tracebacks). Regression for the
fire-and-forget fix.
"""

from __future__ import annotations

import httpx
import pytest

from subarr.integrations import IntegrationError
from subarr.integrations.ollama import OllamaClient
from subarr.routers.integrations import _probe


class _FakeBazarr:
    def __init__(self, *, status_exc=None, badges_exc=None, status_val=None, badges_val=None):
        self._status_exc = status_exc
        self._badges_exc = badges_exc
        self._status_val = status_val or {"version": "1.5.6"}
        self._badges_val = badges_val or {"episodes": 7}

    def is_configured(self) -> bool:
        return True

    async def status(self):
        if self._status_exc:
            raise self._status_exc
        return self._status_val

    async def badges(self):
        if self._badges_exc:
            raise self._badges_exc
        return self._badges_val


@pytest.mark.asyncio
async def test_both_ok_returns_online_with_badges():
    out = await _probe("bazarr", _FakeBazarr(), "bazarr_badges")
    assert out["online"] is True
    assert out["badges"] == {"episodes": 7}
    assert out["version"] == "1.5.6"


@pytest.mark.asyncio
async def test_status_failure_marks_offline():
    out = await _probe(
        "bazarr",
        _FakeBazarr(status_exc=IntegrationError("bazarr /api/system/status: timeout")),
        "bazarr_badges",
    )
    assert out["online"] is False
    # #261: the raw exception text must NOT leak to the client; a safe,
    # constant message is returned instead (real detail goes to the server log).
    assert out["error"]
    assert "timeout" not in out["error"]
    assert "/api/system/status" not in out["error"]


@pytest.mark.asyncio
async def test_badges_blip_stays_online_empty_badges():
    # Status OK but the cosmetic badges endpoint timed out — Bazarr is up;
    # serve it online with empty badges, NOT offline, and with no dangling task.
    out = await _probe(
        "bazarr",
        _FakeBazarr(badges_exc=IntegrationError("bazarr /api/badges: ")),
        "bazarr_badges",
    )
    assert out["online"] is True
    assert out["badges"] == {}


@pytest.mark.asyncio
async def test_both_fail_marks_offline_no_dangling_task():
    # Both blip (the exact scenario that produced the unretrieved-task
    # traceback). gather(return_exceptions=True) retrieves both → offline,
    # no warning. (pytest is configured to error on unraisable exceptions,
    # so a dangling task would fail this test.)
    out = await _probe(
        "bazarr",
        _FakeBazarr(
            status_exc=IntegrationError("bazarr /api/system/status: "),
            badges_exc=IntegrationError("bazarr /api/badges: "),
        ),
        "bazarr_badges",
    )
    assert out["online"] is False


# The ollama probe must cost ONE /api/tags per poll, not two.


@pytest.mark.asyncio
async def test_ollama_probe_fetches_tags_once():
    """The ollama probe used to GET /api/tags TWICE on every poll: once for the
    model list, then it reset the vision cache and called resolve_vision_model(),
    which re-fetched the identical payload through installed_models(). The
    Settings page polls this every 8s, so that is a doubled request forever for
    data already in hand. The resolution itself must still work, hence the
    vision_model_resolved assertion below."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={"models": [{"name": "qwen2.5vl:7b"}, {"name": "qwen2.5:7b"}]},
            )
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.33.3"})
        return httpx.Response(404)

    c = OllamaClient(base_url="http://ollama.test", model="qwen2.5:7b", vision_model="auto")
    c._configured = True
    c._client = httpx.AsyncClient(base_url="http://ollama.test", transport=httpx.MockTransport(handler))

    out = await _probe("ollama", c, "ollama_models")

    assert out["online"] is True
    assert out["badges"]["models"] == 2
    assert out["badges"]["vision_model_resolved"] == "qwen2.5vl:7b"
    assert calls.count("/api/tags") == 1, "expected 1 /api/tags per probe, got %d: %r" % (
        calls.count("/api/tags"),
        calls,
    )
