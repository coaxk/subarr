"""IntegrationBundle.media_servers -- fan-out list of MediaServer clients (#71)."""

from __future__ import annotations

from subarr.coverage_engine import IntegrationBundle
from subarr.integrations.media_server import MediaServer


def test_bundle_media_servers_lists_plex(subarr_env):
    b = IntegrationBundle()
    servers = b.media_servers
    assert isinstance(servers, list) and len(servers) == 1
    assert servers[0] is b.plex
    assert isinstance(servers[0], MediaServer)
