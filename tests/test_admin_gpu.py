"""Tests for /api/gpu, /api/restart, /api/container, /api/plex/scan, /api/logs/events."""

from __future__ import annotations

import json

import httpx
import pytest


# ───── GPU ──────────────────────────────────────────────────────────────────


def test_gpu_returns_offline_when_smi_missing(app_with_stub, monkeypatch):
    # Force nvidia-smi resolution to fail.
    from subarr.routers import gpu as gpu_mod

    monkeypatch.setattr(gpu_mod, "_nvidia_smi_path", lambda: None)

    r = app_with_stub.get("/api/gpu")
    assert r.status_code == 200
    body = r.json()
    assert body["online"] is False
    assert "not found" in body["error"]


def test_gpu_parses_csv_output(app_with_stub, monkeypatch):
    from subarr.routers import gpu as gpu_mod

    async def fake_run_smi(exe, args):
        if any(a.startswith("--query-gpu") for a in args):
            return "NVIDIA RTX 4090, 8192, 24576, 16384, 35, 12, 62, 220.5, 450.0, 8.9, 576.02\n"
        if any(a.startswith("--query-compute-apps") for a in args):
            return "12345, python.exe, 7000\n"
        return ""

    monkeypatch.setattr(gpu_mod, "_nvidia_smi_path", lambda: "fake-smi")
    monkeypatch.setattr(gpu_mod, "_run_smi", fake_run_smi)

    r = app_with_stub.get("/api/gpu")
    assert r.status_code == 200
    body = r.json()
    assert body["online"] is True
    assert body["name"] == "NVIDIA RTX 4090"
    assert body["memory"]["used_mib"] == 8192.0
    assert body["memory"]["total_mib"] == 24576.0
    assert body["utilization"]["gpu_pct"] == 35.0
    assert body["temperature_c"] == 62.0
    assert body["compute_cap"] == 8.9
    assert body["driver_version"] == "576.02"
    assert body["processes"] == [{"pid": 12345, "name": "python.exe", "memory_mib": 7000.0}]


def test_gpu_falls_back_to_legacy_fields_on_old_driver(app_with_stub, monkeypatch):
    """Old drivers reject compute_cap as a query field (non-zero exit). The
    endpoint must retry with the legacy 9-field set so the vitals footer
    survives, returning null for the new fields."""
    from subarr.routers import gpu as gpu_mod

    queries = []

    async def fake_run_smi(exe, args):
        if any(a.startswith("--query-gpu") for a in args):
            query = next(a for a in args if a.startswith("--query-gpu"))
            queries.append(query)
            if "compute_cap" in query:
                raise RuntimeError('nvidia-smi exit 6: "compute_cap" is not a valid field to query')
            return "NVIDIA GTX 1080, 4096, 8192, 4096, 20, 10, 55, 110.0, 180.0\n"
        if any(a.startswith("--query-compute-apps") for a in args):
            return ""
        return ""

    monkeypatch.setattr(gpu_mod, "_nvidia_smi_path", lambda: "fake-smi")
    monkeypatch.setattr(gpu_mod, "_run_smi", fake_run_smi)

    r = app_with_stub.get("/api/gpu")
    assert r.status_code == 200
    body = r.json()
    assert body["online"] is True
    assert body["name"] == "NVIDIA GTX 1080"
    assert body["memory"]["total_mib"] == 8192.0
    assert body["compute_cap"] is None
    assert body["driver_version"] is None
    # Assert the RETRY semantics, not an exact global count: the background
    # dashboard refresh's gpu tile also hits the mocked _run_smi (each
    # gpu_status does the same attempt+retry), so `queries` may carry extra
    # pairs. What matters is that THIS path tried the 11-field query (with
    # compute_cap) and then fell back to the legacy set (without it).
    assert any("compute_cap" in q for q in queries)  # the 11-field attempt that failed
    assert any("compute_cap" not in q for q in queries)  # the legacy retry


# ───── Container info + restart ─────────────────────────────────────────────


def test_container_info(app_with_stub):
    r = app_with_stub.get("/api/container")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "subgen"
    assert body["running"] is True


def test_restart_subgen(app_with_stub):
    r = app_with_stub.post("/api/restart")
    assert r.status_code == 200
    body = r.json()
    assert body["restarted"] is True
    assert body["container"]["running"] is True


@pytest.mark.docker_stub(container_unavailable=True)
def test_restart_returns_503_when_docker_down(app_with_stub):
    r = app_with_stub.post("/api/restart")
    assert r.status_code == 503


# ───── Plex scan ────────────────────────────────────────────────────────────


def _plex_handler(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/library/sections/all/refresh":
        return httpx.Response(200, content=b"")
    if req.url.path == "/queue":
        return httpx.Response(
            200,
            json={
                "queued": [],
                "processing": [],
                "queued_count": 0,
                "processing_count": 0,
                "idle": True,
                "version": "test",
            },
        )
    if req.url.path == "/batch":
        return httpx.Response(200, json={"walked": 0, "queued": 0, "skipped": 0})
    return httpx.Response(404)


@pytest.mark.subgen(handler=_plex_handler)
def test_plex_scan_calls_correct_url(app_with_stub):
    """v1.1.1 refactor: /api/plex/scan now delegates to PlexClient.full_scan().
    #290: PlexClient now holds a persistent httpx client, so we inject a
    MockTransport directly into plex._client rather than patching the
    httpx.AsyncClient class globally."""
    from subarr.integrations.plex import PlexClient

    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(200, content=b"")

    # Inject a configured Plex client into app state (conftest stub leaves
    # it unconfigured by default so other tests get 503 properly).
    plex = PlexClient(base_url="http://plex.test:32400", token="test-token", default_section="all")
    # Swap the persistent client's transport to the mock — no global patching.
    plex._client = httpx.AsyncClient(
        base_url="http://plex.test:32400",
        transport=httpx.MockTransport(handler),
    )
    app_with_stub.app.state.integrations.plex = plex

    r = app_with_stub.post("/api/plex/scan")

    assert r.status_code == 200
    body = r.json()
    assert body["triggered"] is True
    assert body["section"] == "all"
    assert body["scope"] == "full"
    assert len(calls) == 1
    req = calls[0]
    assert req.url.path == "/library/sections/all/refresh"
    assert req.url.params["X-Plex-Token"] == "test-token"


def test_plex_scan_503_when_token_missing(app_with_stub):
    """v1.1.1 refactor: PlexClient.is_configured() returns False when token is
    empty (default in tests via conftest stub). Endpoint returns 503."""
    r = app_with_stub.post("/api/plex/scan")
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"].lower()


# ───── Logs SSE ─────────────────────────────────────────────────────────────


@pytest.mark.docker_stub(log_lines=["INFO:root:hello", "ERROR:root:bad thing happened"])
def test_logs_sse_streams_lines(app_with_stub):
    with app_with_stub.stream("GET", "/api/logs/events?tail=10") as resp:
        assert resp.status_code == 200
        events: list[tuple[str, str]] = []
        current = None
        for chunk in resp.iter_lines():
            if not chunk:
                continue
            if chunk.startswith("event:"):
                current = chunk.split(":", 1)[1].strip()
            elif chunk.startswith("data:"):
                events.append((current, json.loads(chunk.split(":", 1)[1].strip())))

    assert ("log", "INFO:root:hello") in events
    assert ("log", "ERROR:root:bad thing happened") in events


@pytest.mark.docker_stub(container_unavailable=True)
def test_logs_sse_emits_error_when_docker_down(app_with_stub):
    with app_with_stub.stream("GET", "/api/logs/events") as resp:
        assert resp.status_code == 200
        events = []
        current = None
        for chunk in resp.iter_lines():
            if not chunk:
                continue
            if chunk.startswith("event:"):
                current = chunk.split(":", 1)[1].strip()
            elif chunk.startswith("data:"):
                events.append((current, json.loads(chunk.split(":", 1)[1].strip())))
                break

    assert events
    assert events[0][0] == "error"
