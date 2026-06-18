"""#202 slice 2: onboarding-funnel fields + pre-send subgen refresh in telemetry."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from subarr.migrate import run_migrations
from subarr.telemetry import (
    TelemetryCollector,
    _onboarding_complete,
    _onboarding_step,
)


def _collector(tmp_path, **kw):
    p = tmp_path / "t.db"
    run_migrations(p)
    return TelemetryCollector(db_path=p, endpoint="", subarr_version="vT", **kw)


# ─── onboarding signal helpers ──────────────────────────────────────


def test_onboarding_helpers_read_state():
    app_state = SimpleNamespace(
        onboarding=SimpleNamespace(get=lambda: SimpleNamespace(step=4, is_complete=True))
    )
    assert _onboarding_step(app_state) == 4
    assert _onboarding_complete(app_state) is True


def test_onboarding_helpers_best_effort():
    # No onboarding store (or it raises) → safe defaults, never crash the payload.
    assert _onboarding_step(SimpleNamespace()) is None
    assert _onboarding_complete(SimpleNamespace()) is False


# ─── payload carries the fields ─────────────────────────────────────


def test_payload_carries_onboarding_fields(tmp_path):
    c = _collector(tmp_path, stats_provider=lambda: {"onboarding_step": 2, "onboarding_complete": False})
    p = c.build_payload()
    assert p.onboarding_step == 2
    assert p.onboarding_complete is False
    d = p.to_dict()
    assert d["onboarding_step"] == 2
    assert d["onboarding_complete"] is False


# ─── pre-send subgen refresh (the "before-the-fact" fix) ────────────


@pytest.mark.asyncio
async def test_send_now_awaits_refresher(tmp_path):
    called = []

    async def _refresh():
        called.append(1)

    c = _collector(tmp_path, subgen_caps_refresher=_refresh)
    await c.send_now()  # endpoint="" → no POST, but the refresher must run
    assert called == [1]


@pytest.mark.asyncio
async def test_send_now_survives_refresher_error(tmp_path):
    async def _boom():
        raise RuntimeError("probe failed")

    c = _collector(tmp_path, subgen_caps_refresher=_boom)
    ok, err = await c.send_now()  # best-effort — must not raise
    assert ok is False
