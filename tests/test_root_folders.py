"""#134 Phase 0: root-folder plumbing + the consolidated arr-prefix strip.

- Sonarr/Radarr `root_folders()` hit GET /api/v3/rootfolder (MockTransport,
  the bazarr-client test pattern).
- GET /api/onboarding/root-folders is best-effort per service: unconfigured
  services report configured=false instead of erroring the whole payload.
- paths.strip_arr_prefix is the single consolidated copy of what previously
  lived duplicated in coverage_engine / scheduler / coverage_actions (+ a
  fourth consumer, arr_mediainfo) — behavior pinned here.
"""

from __future__ import annotations

import asyncio

import httpx

from subarr.integrations.radarr import RadarrClient
from subarr.integrations.sonarr import SonarrClient
from subarr.paths import strip_arr_prefix


def _wire(client, handler):
    client._client = httpx.AsyncClient(base_url="http://arr:8989", transport=httpx.MockTransport(handler))
    client._configured = True
    return client


def test_sonarr_root_folders_hits_rootfolder_endpoint():
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["path"] = req.url.path
        return httpx.Response(200, json=[{"path": "/tv", "accessible": True, "freeSpace": 123}])

    rows = asyncio.run(_wire(SonarrClient(), handler).root_folders())
    assert captured["path"] == "/api/v3/rootfolder"
    assert rows[0]["path"] == "/tv"


def test_radarr_root_folders_hits_rootfolder_endpoint():
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["path"] = req.url.path
        return httpx.Response(200, json=[{"path": "/movies", "accessible": True}])

    rows = asyncio.run(_wire(RadarrClient(), handler).root_folders())
    assert captured["path"] == "/api/v3/rootfolder"
    assert rows[0]["path"] == "/movies"


def test_root_folders_endpoint_reports_unconfigured(app_with_stub):
    body = app_with_stub.get("/api/onboarding/root-folders").json()
    for svc in ("sonarr", "radarr"):
        assert body[svc]["configured"] is False
        assert body[svc]["folders"] == []


def test_root_folders_endpoint_surfaces_folders(app_with_stub):
    class FakeArr:
        def is_configured(self):
            return True

        async def root_folders(self):
            return [{"path": "/data/tv", "accessible": True, "freeSpace": 42}]

        async def aclose(self):  # lifespan teardown closes integration clients
            pass

    app_with_stub.app.state.integrations.sonarr = FakeArr()
    body = app_with_stub.get("/api/onboarding/root-folders").json()
    assert body["sonarr"]["configured"] is True
    assert body["sonarr"]["folders"] == [{"path": "/data/tv", "accessible": True, "free_space": 42}]
    assert body["radarr"]["configured"] is False


def test_root_folders_endpoint_isolates_per_service_errors(app_with_stub):
    class BrokenArr:
        def is_configured(self):
            return True

        async def root_folders(self):
            raise RuntimeError("arr down")

        async def aclose(self):
            pass

    app_with_stub.app.state.integrations.radarr = BrokenArr()
    body = app_with_stub.get("/api/onboarding/root-folders").json()
    assert body["radarr"]["configured"] is True
    assert body["radarr"]["folders"] == []
    # #261: per-service failure still isolates the error, but the raw exception
    # text is sanitized out of the response (no leak to the client).
    assert body["radarr"]["error"]
    assert "arr down" not in body["radarr"]["error"]
    assert body["sonarr"]["configured"] is False  # the other service unaffected


# ── strip_arr_prefix (consolidated) ──────────────────────────────────


def test_strip_arr_prefix_default_prefix_comes_from_settings():
    # Settings is a frozen singleton — exercise the default-wiring against
    # whatever prefix is configured rather than monkeypatching it.
    from subarr.paths import settings

    pfx = settings.arr_path_prefix
    assert strip_arr_prefix(f"{pfx}TV/Foo") == "TV/Foo"


def test_strip_arr_prefix_passthrough_without_match():
    assert strip_arr_prefix("/other/TV/Foo", prefix="/data/Media/") == "other/TV/Foo"


def test_strip_arr_prefix_falsy_passthrough():
    assert strip_arr_prefix(None) is None
    assert strip_arr_prefix("") == ""


def test_strip_arr_prefix_explicit_prefix_overrides_settings():
    assert strip_arr_prefix("/mnt/tv/Show", prefix="/mnt/") == "tv/Show"
