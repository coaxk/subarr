"""IntegrationBundle.media_servers -- fan-out list of MediaServer clients (#71)."""

from __future__ import annotations

import importlib

from subarr.coverage_engine import IntegrationBundle
from subarr.integrations.media_server import MediaServer


def test_bundle_media_servers_lists_plex(subarr_env):
    b = IntegrationBundle()
    servers = b.media_servers
    assert isinstance(servers, list) and len(servers) == 2
    assert servers[0] is b.plex
    assert isinstance(servers[0], MediaServer)


def test_media_servers_includes_jellyfin_when_configured(subarr_env, monkeypatch):
    monkeypatch.setenv("JELLYFIN_URL", "http://jf.test:8096")
    monkeypatch.setenv("JELLYFIN_API_KEY", "jf-test-key")

    from subarr import config, coverage_engine

    importlib.reload(config)
    importlib.reload(coverage_engine)

    b = coverage_engine.IntegrationBundle()
    types = [s.type for s in b.media_servers]
    assert "plex" in types and "jellyfin" in types

    configured = [s.type for s in b.media_servers if s.is_configured()]
    assert "jellyfin" in configured
