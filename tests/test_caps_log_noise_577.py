"""#577: the capabilities line is a state report, so log it on CHANGE.

`SubgenWatchdog` re-probes every 30 s and `probe_capabilities()` logged its full
result at INFO every time, so a healthy install wrote the same line about 2,880
times a day. Reading 18 hours of log to answer "what did subarr do overnight?"
meant reading almost nothing else, which is the cost: real events are buried,
not merely accompanied.

It now logs at INFO the first time, whenever a reported value changes, and on
recovery after an unreachable window; an unchanged re-probe goes to DEBUG.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from subarr.subgen_client import SubgenClient

CAPS_LINE = "subgen capabilities:"


def _client(handler) -> SubgenClient:
    c = SubgenClient(base_url="http://fake-subgen:9000")
    c._client = httpx.AsyncClient(base_url="http://fake-subgen:9000", transport=httpx.MockTransport(handler))
    return c


def _subgen(version: str = "2026.08.1", reachable: bool = True):
    def handler(request: httpx.Request) -> httpx.Response:
        if not reachable:
            raise httpx.ConnectError("refused")
        if request.url.path == "/status":
            return httpx.Response(
                200,
                json={
                    "version": f"Subgen {version}, subarr-subgen v4.31",
                },
            )
        if request.url.path == "/queue":
            return httpx.Response(200, json={"queued": [], "processing": []})
        return httpx.Response(404)

    return handler


def _caps_records(caplog, level: int) -> list[str]:
    return [r.message for r in caplog.records if r.levelno == level and CAPS_LINE in r.message]


@pytest.mark.asyncio
async def test_the_first_probe_reports_at_info(caplog):
    caplog.set_level(logging.DEBUG, logger="subarr.subgen_client")
    await _client(_subgen()).probe_capabilities()
    assert len(_caps_records(caplog, logging.INFO)) == 1


@pytest.mark.asyncio
async def test_an_unchanged_re_probe_does_not_repeat_the_info_line(caplog):
    caplog.set_level(logging.DEBUG, logger="subarr.subgen_client")
    c = _client(_subgen())
    for _ in range(5):
        await c.probe_capabilities()
    assert len(_caps_records(caplog, logging.INFO)) == 1


@pytest.mark.asyncio
async def test_an_unchanged_re_probe_is_still_visible_at_debug(caplog):
    """Silence would be its own problem: at DEBUG the probe is still traceable."""
    caplog.set_level(logging.DEBUG, logger="subarr.subgen_client")
    c = _client(_subgen())
    await c.probe_capabilities()
    await c.probe_capabilities()
    assert len(_caps_records(caplog, logging.DEBUG)) == 1


@pytest.mark.asyncio
async def test_a_changed_capability_reports_at_info_again(caplog):
    caplog.set_level(logging.DEBUG, logger="subarr.subgen_client")
    c = _client(_subgen(version="2026.08.1"))
    await c.probe_capabilities()
    c._client = httpx.AsyncClient(
        base_url="http://fake-subgen:9000",
        transport=httpx.MockTransport(_subgen(version="2026.09.2")),
    )
    await c.probe_capabilities()
    lines = _caps_records(caplog, logging.INFO)
    assert len(lines) == 2
    assert "2026.09.2" in lines[1]


@pytest.mark.asyncio
async def test_recovery_after_an_outage_reports_at_info(caplog):
    """The caps are identical either side of the outage, but "subgen is back" is
    an event worth a line."""
    caplog.set_level(logging.DEBUG, logger="subarr.subgen_client")
    c = _client(_subgen())
    await c.probe_capabilities()
    c._client = httpx.AsyncClient(
        base_url="http://fake-subgen:9000",
        transport=httpx.MockTransport(_subgen(reachable=False)),
    )
    await c.probe_capabilities()
    c._client = httpx.AsyncClient(
        base_url="http://fake-subgen:9000", transport=httpx.MockTransport(_subgen())
    )
    await c.probe_capabilities()
    assert len(_caps_records(caplog, logging.INFO)) >= 2


@pytest.mark.asyncio
async def test_two_clients_do_not_share_the_suppression(caplog):
    """Each client reports its own first probe: a second subgen (onboarding's
    test client) must not be silenced by the live one."""
    caplog.set_level(logging.DEBUG, logger="subarr.subgen_client")
    await _client(_subgen()).probe_capabilities()
    await _client(_subgen()).probe_capabilities()
    assert len(_caps_records(caplog, logging.INFO)) == 2


@pytest.mark.asyncio
async def test_the_line_still_carries_the_values(caplog):
    """Quieter must not mean less informative: the line that IS logged says
    everything it used to."""
    caplog.set_level(logging.DEBUG, logger="subarr.subgen_client")
    await _client(_subgen()).probe_capabilities()
    (line,) = _caps_records(caplog, logging.INFO)
    for field in ("version=", "patch_rev=", "has_queue=", "has_batch=", "is_subarr_subgen=", "compat_mode="):
        assert field in line
