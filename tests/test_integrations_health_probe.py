"""_probe() bazarr_badges path: status + badges run concurrently, but BOTH
results must always be retrieved so a Bazarr blip never leaves a dangling task
('Task exception was never retrieved' tracebacks). Regression for the
fire-and-forget fix.
"""

from __future__ import annotations

import pytest

from subarr.integrations import IntegrationError
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
