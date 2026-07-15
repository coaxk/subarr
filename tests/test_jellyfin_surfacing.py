"""Slice 2b, Task 1 (#71): register Jellyfin in the integration framework.

Covers the credential-editor config endpoint, the test-connection endpoint,
and the health probe fan-out — mirrors the existing Plex coverage in
tests/test_integration_credentials.py so Jellyfin gets the same UI wiring
(configurable + testable from Settings) that Plex already has.
"""

from __future__ import annotations


def test_jellyfin_test_endpoint_reports_unreachable_cleanly(app_with_stub):
    r = app_with_stub.post(
        "/api/integrations/jellyfin/test",
        json={"url": "http://127.0.0.1:59999", "api_key": "x"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_jellyfin_test_endpoint_requires_creds(app_with_stub):
    r = app_with_stub.post("/api/integrations/jellyfin/test", json={"url": "", "api_key": ""})
    assert r.status_code == 200 and r.json()["ok"] is False


def test_jellyfin_config_endpoint_known(app_with_stub):
    r = app_with_stub.get("/api/integrations/jellyfin/config")
    assert r.status_code == 200
    body = r.json()
    fields = body["fields"]
    assert "url" in fields and "api_key" in fields


def test_health_includes_jellyfin(app_with_stub):
    r = app_with_stub.get("/api/integrations/health")
    names = {i["name"] for i in r.json().get("integrations", [])}
    assert "jellyfin" in names


# ─── Slice 2c, Task 4 (#71): Home dashboard tile ─────────────────────
#
# The dashboard tile list (/api/home/dashboard) is a DIFFERENT probe set
# from /api/integrations/health above — it must NOT show a jellyfin tile
# for Plex-only installs (no dead "not configured" tile clutter), but
# MUST show one once Jellyfin is configured.


def test_dashboard_omits_jellyfin_tile_when_unconfigured(app_with_stub):
    r = app_with_stub.get("/api/home/dashboard?fresh=true")
    assert r.status_code == 200
    names = {i["name"] for i in r.json().get("integrations", [])}
    assert "jellyfin" not in names


def test_dashboard_includes_jellyfin_tile_when_configured(app_with_stub, monkeypatch):
    app = app_with_stub.app
    jellyfin = app.state.integrations.jellyfin
    monkeypatch.setattr(jellyfin, "is_configured", lambda: True)

    async def fake_status():
        return {"version": "10.11.11", "server_name": "test-jf"}

    monkeypatch.setattr(jellyfin, "status", fake_status)

    r = app_with_stub.get("/api/home/dashboard?fresh=true")
    assert r.status_code == 200
    names = {i["name"] for i in r.json().get("integrations", [])}
    assert "jellyfin" in names
