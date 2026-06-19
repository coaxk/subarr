"""Tests for the telemetry collector.

Covers:
  - install_id generated once, stable across restarts
  - Default state: opted_in=True (v1.0 decision)
  - opt-out persists across new collector instances
  - build_payload pulls from stats_provider + subgen_caps_provider
  - send_now without endpoint: no transmission, records what-would-send
  - send_now with mock endpoint: posts, records last_payload
  - send_now while opted-out: no-op, no transmission
  - send_now on HTTP failure: error recorded, last_payload preserved
  - state() returns hydrated TelemetryState
  - last_payload parses back to dict
  - to_dict never includes raw install_id/secrets we said we wouldn't send
    (smoke check that the contract holds — useful for future-proofing)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from subarr.migrate import run_migrations
from subarr.telemetry import TelemetryCollector


@dataclass
class _FakeCaps:
    reachable: bool = True
    version: str | None = "2026.05.3"
    is_subarr_subgen: bool = True


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "subarr.db"
    run_migrations(p)
    return p


def _make_collector(
    db_path: Path,
    endpoint: str = "",
    stats: dict | None = None,
    caps: _FakeCaps | None = None,
) -> TelemetryCollector:
    return TelemetryCollector(
        db_path=db_path,
        endpoint=endpoint,
        subarr_version="v1.0.0",
        stats_provider=lambda: stats or {},
        subgen_caps_provider=lambda: caps if caps else _FakeCaps(),
    )


# ─── State + persistence ───────────────────────────────────────────


def test_install_id_generated_on_first_boot(db_path: Path):
    c = _make_collector(db_path)
    st = c.state()
    assert len(st.install_id) == 32  # uuid4().hex
    assert st.opted_in is True  # v1.0 default
    assert st.last_ping_at is None


def test_install_id_stable_across_restarts(db_path: Path):
    c1 = _make_collector(db_path)
    id1 = c1.state().install_id
    # Simulate restart: new collector against same DB
    c2 = _make_collector(db_path)
    id2 = c2.state().install_id
    assert id1 == id2


def test_opt_out_persists(db_path: Path):
    c1 = _make_collector(db_path)
    c1.set_opt_in(False)
    assert c1.state().opted_in is False
    # Restart
    c2 = _make_collector(db_path)
    assert c2.state().opted_in is False


def test_opt_in_after_opt_out(db_path: Path):
    c = _make_collector(db_path)
    c.set_opt_in(False)
    c.set_opt_in(True)
    assert c.state().opted_in is True


# ─── Payload assembly ──────────────────────────────────────────────


def test_payload_contains_expected_fields(db_path: Path):
    stats = {
        "integrations": {
            "bazarr": True,
            "sonarr": True,
            "radarr": False,
            "tautulli": False,
            "plex": False,
            "ollama": True,
        },
        "library_bucket": "1k-10k",
        "scheduler_enabled": True,
        "scheduler_mode": "manual_confirm",
        "walks_per_day_30d": 1.5,
        "error_counts_30d": {"SubgenUnavailable": 2},
        "docker_tier": 2,
    }
    c = _make_collector(db_path, stats=stats, caps=_FakeCaps())
    p = c.build_payload()
    d = p.to_dict()

    assert d["subarr_version"] == "v1.0.0"
    assert d["subgen_kind"] == "subarr-subgen"
    assert d["subgen_version"] == "2026.05.3"
    assert d["integrations"]["bazarr"] is True
    assert d["library_bucket"] == "1k-10k"
    assert d["scheduler_enabled"] is True
    assert d["scheduler_mode"] == "manual_confirm"
    assert d["walks_per_day_30d"] == 1.5
    assert d["error_counts_30d"]["SubgenUnavailable"] == 2
    assert d["docker_tier"] == 2
    # install_id is the only identifier
    assert d["install_id"] == c.state().install_id


def test_payload_never_includes_forbidden_fields(db_path: Path):
    """Smoke: the contract is 'never send paths, titles, IPs, keys'.
    If a future change accidentally adds one of those fields, this
    test surfaces it."""
    c = _make_collector(db_path)
    d = c.build_payload().to_dict()
    forbidden = {
        "path",
        "title",
        "ip",
        "api_key",
        "url",
        "hostname",
        "email",
        "username",
        "password",
        "token",
    }
    for key in d.keys():
        for f in forbidden:
            assert f not in key.lower(), f"forbidden field name leaked: {key}"


def test_subgen_kind_vanilla_when_not_patched(db_path: Path):
    c = _make_collector(db_path, caps=_FakeCaps(is_subarr_subgen=False))
    d = c.build_payload().to_dict()
    assert d["subgen_kind"] == "vanilla"


def test_subgen_kind_unreachable_when_not_reachable(db_path: Path):
    c = _make_collector(db_path, caps=_FakeCaps(reachable=False))
    d = c.build_payload().to_dict()
    assert d["subgen_kind"] == "unreachable"
    assert d["subgen_version"] is None


# ─── send_now behaviour ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_now_records_locally_when_no_endpoint(db_path: Path):
    """No endpoint configured: still record what we'd send so the
    Settings preview has data, but don't transmit."""
    c = _make_collector(db_path, endpoint="")
    sent, err = await c.send_now()
    await c.stop()

    assert sent is False
    assert err is None
    st = c.state()
    # last_payload populated (we recorded locally)
    assert st.last_payload is not None
    assert st.last_payload["subarr_version"] == "v1.0.0"
    # last_ping_at NOT set (we didn't transmit)
    assert st.last_ping_at is None


@pytest.mark.asyncio
async def test_send_now_opted_out_is_noop(db_path: Path):
    c = _make_collector(db_path, endpoint="http://x")
    c.set_opt_in(False)
    sent, err = await c.send_now()
    await c.stop()

    assert sent is False
    assert err is None
    st = c.state()
    # We didn't even record locally — user opted out, no collection.
    assert st.last_payload is None


@pytest.mark.asyncio
async def test_send_now_posts_to_endpoint(db_path: Path):
    """With endpoint configured + opted in, send POSTs JSON + records."""
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["body"] = req.content.decode()
        return httpx.Response(200, json={"ok": True})

    c = _make_collector(db_path, endpoint="http://telemetry.example/ping")
    c._client = httpx.AsyncClient(
        timeout=5.0,
        transport=httpx.MockTransport(handler),
    )
    sent, err = await c.send_now()
    await c.stop()

    assert sent is True
    assert err is None
    assert captured["url"].endswith("/ping")
    # Body is the JSON payload
    import json as _json

    body = _json.loads(captured["body"])
    assert body["subarr_version"] == "v1.0.0"
    # last_ping_at recorded
    assert c.state().last_ping_at is not None


@pytest.mark.asyncio
async def test_send_now_http_error_recorded(db_path: Path):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    c = _make_collector(db_path, endpoint="http://telemetry.example/ping")
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sent, err = await c.send_now()
    await c.stop()

    assert sent is False
    assert err == "HTTP 503"
    st = c.state()
    assert st.last_error == "HTTP 503"
    # Payload still recorded — Settings can show what we tried to send.
    assert st.last_payload is not None


@pytest.mark.asyncio
async def test_send_now_network_error_recorded(db_path: Path):
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=req)

    c = _make_collector(db_path, endpoint="http://telemetry.example/ping")
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sent, err = await c.send_now()
    await c.stop()

    assert sent is False
    assert "ConnectError" in err
    assert "ConnectError" in c.state().last_error


# ─── last_payload deserialisation ──────────────────────────────────


@pytest.mark.asyncio
async def test_last_payload_parses_back_to_dict(db_path: Path):
    c = _make_collector(db_path, endpoint="")  # no transmission, just record
    await c.send_now()
    await c.stop()
    st = c.state()
    assert isinstance(st.last_payload, dict)
    assert st.last_payload["install_id"] == st.install_id


# ─── make_default_stats_provider: ollama configured signal (#119) ───
#
# Regression: ollama was reported "configured" for EVERY install because it
# gated on bool(settings.ollama_url), which defaults to http://ollama:11434.
# It must instead reflect REAL reachability from the cached integrations-
# health probe (app.state.ollama_probe_result.reachable), mirroring the
# subgen_caps pattern.


from types import SimpleNamespace

from subarr.telemetry import make_default_stats_provider


def _fake_app_state(db_path: Path, ollama_probe_result) -> SimpleNamespace:
    """Minimal app.state stand-in for make_default_stats_provider.

    Only the attributes the provider touches are wired; everything that
    can raise is left absent so the provider's try/except fallbacks fire
    and we isolate the ollama-reachability assertion."""
    from subarr.probe_store import ProbeStore
    from subarr.scan_store import ScanStore
    from subarr.error_store import ErrorStore
    from subarr.schedule_store import ScheduleStore

    return SimpleNamespace(
        probe_store=ProbeStore(db_path),
        scans=ScanStore(db_path),
        errors=ErrorStore(db_path),
        schedule=ScheduleStore(db_path),
        ollama_probe_result=ollama_probe_result,
    )


def test_ollama_configured_true_when_probe_reachable(db_path: Path):
    """Cached probe says ollama is reachable → reported configured."""
    state = _fake_app_state(db_path, SimpleNamespace(reachable=True))
    provider = make_default_stats_provider(state)
    out = provider()
    assert out["integrations"]["ollama"] is True


def test_ollama_not_configured_when_defaulted_url_but_unreachable(db_path: Path):
    """Defaulted OLLAMA_URL but nothing listening → cached probe is
    unreachable → NOT reported configured (the #119 fix). bool(ollama_url)
    would have been True here on every install."""
    from subarr.config import settings

    assert settings.ollama_url  # the default string is non-empty (pre-fix bug)
    state = _fake_app_state(db_path, SimpleNamespace(reachable=False))
    provider = make_default_stats_provider(state)
    out = provider()
    assert out["integrations"]["ollama"] is False


def test_ollama_not_configured_when_probe_never_ran(db_path: Path):
    """No cached probe yet (app.state.ollama_probe_result is None) →
    treat as not-configured rather than falling back to the URL default."""
    state = _fake_app_state(db_path, None)
    provider = make_default_stats_provider(state)
    out = provider()
    assert out["integrations"]["ollama"] is False


# ─── Shape-lock: payload key allowlist ────────────────────────────


# If this test fails, a payload field was added — confirm it carries
# NOTHING user-fingerprintable before adding it here. This is the teeth
# behind privacy-by-construction.
_EXPECTED_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {
        "install_id",
        "sent_at",
        "subarr_version",
        "python_version",
        "os_arch",
        "subgen_kind",
        "subgen_version",
        "integrations",
        "library_bucket",
        "scheduler_enabled",
        "scheduler_mode",
        "walks_per_day_30d",
        "error_counts_30d",
        "crash_counts_24h",
        "install_age_days",
        "data_persistent",
        "docker_tier",
        "onboarding_step",
        "onboarding_complete",
    }
)


def test_payload_shape_locked(db_path: Path):
    """TelemetryPayload.to_dict() must emit EXACTLY these keys — no more,
    no less. Any added key must pass a privacy review before being added to
    _EXPECTED_PAYLOAD_KEYS above."""
    stats = {
        "integrations": {
            "bazarr": True,
            "sonarr": False,
            "radarr": False,
            "tautulli": False,
            "plex": False,
            "ollama": False,
        },
        "library_bucket": "100-1k",
        "scheduler_enabled": False,
        "scheduler_mode": None,
        "walks_per_day_30d": 0.5,
        "error_counts_30d": {},
        "crash_counts_24h": {},
        "data_persistent": True,
        "docker_tier": 2,
        "onboarding_step": 3,
        "onboarding_complete": False,
    }
    c = _make_collector(db_path, stats=stats, caps=_FakeCaps())
    d = c.build_payload().to_dict()
    assert set(d.keys()) == _EXPECTED_PAYLOAD_KEYS


# ─── task_health supervision wiring ───────────────────────────────
#
# Both tests exercise the REAL _loop code path by starting the loop with a
# mock transport and letting the initial-send fire, then stopping immediately.
# The loop calls record_success/record_failure on self._health — we stub that
# with a lightweight recorder. No real network traffic: mock transports only.


@pytest.mark.asyncio
async def test_loop_records_success_on_healthy_tick(db_path: Path):
    """_loop's initial send succeeds (HTTP 200) → record_success("telemetry",
    expected_interval_s=PING_INTERVAL_S) is called on self._health."""
    import asyncio

    from subarr.telemetry import PING_INTERVAL_S

    calls: list[tuple] = []

    class _FakeHealth:
        def record_success(self, name: str, *, expected_interval_s: float | None = None) -> None:
            calls.append(("success", name, expected_interval_s))

        def record_failure(
            self, name: str, exc: BaseException, *, expected_interval_s: float | None = None
        ) -> None:
            calls.append(("failure", name, expected_interval_s))

    def _ok_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    c = _make_collector(db_path, endpoint="http://telemetry.example/ping")
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(_ok_handler))
    c._health = _FakeHealth()

    # Run _loop as a task; it sends immediately on boot then waits PING_INTERVAL_S.
    # We give it a moment to complete the initial send, then cancel.
    task = asyncio.create_task(c._loop())
    await asyncio.sleep(0.05)  # let the initial send + health hook fire
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    await c.stop()

    assert any(
        kind == "success" and name == "telemetry" and interval == PING_INTERVAL_S
        for kind, name, interval in calls
    ), f"record_success not called; calls={calls}"


@pytest.mark.asyncio
async def test_loop_records_failure_on_failed_send(db_path: Path):
    """_loop's initial send raises an exception → record_failure("telemetry",
    expected_interval_s=PING_INTERVAL_S) is called on self._health."""
    import asyncio

    from subarr.telemetry import PING_INTERVAL_S

    calls: list[tuple] = []

    class _FakeHealth:
        def record_success(self, name: str, *, expected_interval_s: float | None = None) -> None:
            calls.append(("success", name, expected_interval_s))

        def record_failure(
            self, name: str, exc: BaseException, *, expected_interval_s: float | None = None
        ) -> None:
            calls.append(("failure", name, expected_interval_s))

    def _exploding_handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=req)

    c = _make_collector(db_path, endpoint="http://telemetry.example/ping")
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(_exploding_handler))
    c._health = _FakeHealth()

    # _loop wraps send_now in try/except and calls record_failure on the
    # exception. ConnectError is caught inside send_now and returned as (False,
    # err_str) — send_now itself doesn't raise — so record_failure is NOT
    # triggered by the loop's outer except. Instead record_success is called
    # (send_now returned without raising), but the test below verifies the
    # health hook fires at all (success path since no exception escaped).
    # To exercise record_failure, patch send_now to raise directly.
    async def _raising_send():
        raise RuntimeError("simulated send crash")

    c.send_now = _raising_send  # type: ignore[method-assign]

    task = asyncio.create_task(c._loop())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    await c.stop()

    assert any(
        kind == "failure" and name == "telemetry" and interval == PING_INTERVAL_S
        for kind, name, interval in calls
    ), f"record_failure not called; calls={calls}"
