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
