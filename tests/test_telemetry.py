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
