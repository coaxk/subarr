"""#202 follow-up: telemetry must NOT transmit under tests/CI.

The pinger's _loop POSTs immediately on start(), the endpoint default is the
real prod URL, and conftest never disabled it — so every TestClient boot was
pinging telemetry.subarr.com (the Windows/AMD64 flood in the prod D1). These
guard against a regression of that.
"""

from __future__ import annotations

import pytest

from subarr.migrate import run_migrations
from subarr.telemetry import TelemetryCollector


def _collector(db_path, endpoint):
    p = db_path / "t.db"
    run_migrations(p)
    return TelemetryCollector(db_path=p, endpoint=endpoint, subarr_version="vTEST")


def test_start_is_noop_without_endpoint(tmp_path):
    # Disabled telemetry (no endpoint) must not spawn the pinger — its first
    # tick would otherwise POST to whatever endpoint is configured.
    c = _collector(tmp_path, "")
    c.start()
    assert c._task is None


@pytest.mark.asyncio
async def test_start_spawns_pinger_when_endpoint_set(tmp_path, monkeypatch):
    c = _collector(tmp_path, "https://telemetry.example/ping")

    async def _noop():
        return (False, None)

    monkeypatch.setattr(c, "send_now", _noop)  # never touch the network in tests
    c.start()
    assert c._task is not None
    await c.stop()


def test_test_env_disables_telemetry_endpoint():
    # Regression guard: conftest hard-disables the baked prod endpoint so the
    # suite can never ping telemetry.subarr.com again.
    from subarr.config import settings

    assert settings.telemetry_endpoint == ""
