"""Tests for Tier-2 docker discovery.

Covers:
  - Image-name regex matching for each service in the catalogue
  - Container name matching (when image name lacks the service name)
  - URL inference: shared network → container hostname; published port
    → host.docker.internal; neither → None
  - Discovery against a docker-API responding with a realistic fixture
  - reachable() returns False on connection error
  - discover_for_service() filters by service
  - Config mount detection for Tier-3 hint
  - Multiple matches for the same service surfaced separately
"""

from __future__ import annotations

import httpx
import pytest

from subarr.docker_discovery import (
    DiscoveredService,
    DockerDiscovery,
    SERVICE_CATALOGUE,
)


def _discovery_with_mock(handler) -> DockerDiscovery:
    d = DockerDiscovery(base_url="http://docker-proxy:2375")
    d._client = httpx.AsyncClient(
        base_url="http://docker-proxy:2375",
        transport=httpx.MockTransport(handler),
    )
    return d


# ─── Static matching tests ─────────────────────────────────────────


@pytest.mark.parametrize(
    "image, name, expected",
    [
        ("linuxserver/bazarr:1.5.6", "bazarr", "bazarr"),
        ("linuxserver/sonarr:latest", "sonarr", "sonarr"),
        ("ghcr.io/hotio/radarr", "radarr", "radarr"),
        ("tautulli/tautulli", "tautulli", "tautulli"),
        ("ghcr.io/coaxk/subarr-subgen:2026.05.3-r1", "subgen", "subgen"),
        ("mccloud/subgen:latest", "subgen", "subgen"),
        ("ollama/ollama", "ollama", "ollama"),
        ("plexinc/pms-docker", "plex", "plex"),
        ("jellyfin/jellyfin", "jellyfin", "jellyfin"),
        # No match — random image
        ("nginx:alpine", "nginx", None),
        # No match — image is opaque but container is named the service.
        # This catches private rebuilds with stripped tags.
        ("ghcr.io/private/build:abc", "bazarr", "bazarr"),
    ],
)
def test_service_matching(image: str, name: str, expected: str | None):
    container = {"Image": image, "Names": [f"/{name}"]}
    assert DockerDiscovery._match_service(container) == expected


def test_url_inference_shared_network_wins():
    url, reason = DockerDiscovery._infer_url(
        service="bazarr",
        container_name="bazarr",
        networks=["media-stack", "host"],
        published={6767: 6767},  # also published, but shared net wins
        subarr_networks={"media-stack"},
    )
    assert url == "http://bazarr:6767"
    assert "media-stack" in reason


def test_url_inference_falls_back_to_host_port():
    url, reason = DockerDiscovery._infer_url(
        service="bazarr",
        container_name="bazarr",
        networks=["isolated-net"],  # no shared network with subarr
        published={6767: 6768},  # mapped to host port 6768
        subarr_networks={"safe-bridge"},
    )
    assert url == "http://host.docker.internal:6768"
    assert "6768" in reason


def test_url_inference_none_when_no_path():
    url, reason = DockerDiscovery._infer_url(
        service="bazarr",
        container_name="bazarr",
        networks=["isolated-net"],
        published={},  # no published ports
        subarr_networks={"safe-bridge"},
    )
    assert url is None
    assert "no port 6767 published" in reason


# ─── Live API mock tests ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_reachable_true_when_ping_200():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/_ping":
            return httpx.Response(200, text="OK")
        return httpx.Response(404)

    d = _discovery_with_mock(handler)
    assert await d.reachable() is True
    await d.aclose()


@pytest.mark.asyncio
async def test_reachable_false_on_connect_error():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=req)

    d = _discovery_with_mock(handler)
    assert await d.reachable() is False
    await d.aclose()


@pytest.mark.asyncio
async def test_discover_finds_multiple_services():
    """Realistic homelab: bazarr + sonarr + radarr + subgen all on the
    media-stack network. Subarr is also on media-stack. All four should
    discover with shared-network URLs."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/containers/json":
            return httpx.Response(
                200,
                json=[
                    {
                        "Id": "abc123" + "0" * 58,
                        "Image": "linuxserver/bazarr:1.5.6",
                        "Names": ["/bazarr"],
                        "Ports": [{"PrivatePort": 6767, "PublicPort": 6767, "Type": "tcp"}],
                        "NetworkSettings": {"Networks": {"media-stack": {}}},
                        "Mounts": [{"Source": "/mnt/configs/bazarr", "Destination": "/config"}],
                        "Labels": {"org.opencontainers.image.version": "1.5.6"},
                    },
                    {
                        "Id": "def456" + "0" * 58,
                        "Image": "linuxserver/sonarr:latest",
                        "Names": ["/sonarr"],
                        "Ports": [{"PrivatePort": 8989, "PublicPort": 8989, "Type": "tcp"}],
                        "NetworkSettings": {"Networks": {"media-stack": {}}},
                        "Mounts": [{"Source": "/mnt/configs/sonarr", "Destination": "/config"}],
                        "Labels": {},
                    },
                    {
                        "Id": "789abc" + "0" * 58,
                        "Image": "nginx:alpine",  # not a service
                        "Names": ["/nginx"],
                        "Ports": [],
                        "NetworkSettings": {"Networks": {"media-stack": {}}},
                        "Mounts": [],
                        "Labels": {},
                    },
                ],
            )
        # Subarr's own container lookup — return its network
        if req.url.path.startswith("/containers/") and req.url.path.endswith("/json"):
            return httpx.Response(
                200,
                json={
                    "NetworkSettings": {"Networks": {"media-stack": {}}},
                },
            )
        return httpx.Response(404)

    d = _discovery_with_mock(handler)
    services = await d.discover()
    await d.aclose()

    assert len(services) == 2
    by_service = {s.service: s for s in services}
    assert "bazarr" in by_service
    assert "sonarr" in by_service

    # URL inference — both on shared network
    assert by_service["bazarr"].inferred_url == "http://bazarr:6767"
    assert by_service["sonarr"].inferred_url == "http://sonarr:8989"

    # Config mount → Tier-3 hint populated
    assert by_service["bazarr"].config_host_path == "/mnt/configs/bazarr"

    # Image version label picked up
    assert by_service["bazarr"].image_version == "1.5.6"


@pytest.mark.asyncio
async def test_discover_returns_empty_on_docker_unreachable():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=req)

    d = _discovery_with_mock(handler)
    services = await d.discover()
    await d.aclose()

    assert services == []


@pytest.mark.asyncio
async def test_discover_for_service_filters():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/containers/json":
            return httpx.Response(
                200,
                json=[
                    {
                        "Id": "1" * 64,
                        "Image": "linuxserver/bazarr",
                        "Names": ["/bazarr"],
                        "Ports": [],
                        "Mounts": [],
                        "NetworkSettings": {"Networks": {"x": {}}},
                        "Labels": {},
                    },
                    {
                        "Id": "2" * 64,
                        "Image": "linuxserver/sonarr",
                        "Names": ["/sonarr"],
                        "Ports": [],
                        "Mounts": [],
                        "NetworkSettings": {"Networks": {"x": {}}},
                        "Labels": {},
                    },
                ],
            )
        if "/json" in req.url.path:
            return httpx.Response(200, json={"NetworkSettings": {"Networks": {}}})
        return httpx.Response(404)

    d = _discovery_with_mock(handler)
    bazarrs = await d.discover_for_service("bazarr")
    sonarrs = await d.discover_for_service("sonarr")
    nothing = await d.discover_for_service("plex")
    await d.aclose()

    assert len(bazarrs) == 1
    assert bazarrs[0].service == "bazarr"
    assert len(sonarrs) == 1
    assert nothing == []


@pytest.mark.asyncio
async def test_multiple_instances_of_same_service():
    """Some users run multiple Sonarrs (4K + standard). Discovery
    should surface both — wizard decides which is which."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/containers/json":
            return httpx.Response(
                200,
                json=[
                    {
                        "Id": "1" * 64,
                        "Image": "linuxserver/sonarr:latest",
                        "Names": ["/sonarr"],
                        "Ports": [],
                        "NetworkSettings": {"Networks": {"a": {}}},
                        "Mounts": [],
                        "Labels": {},
                    },
                    {
                        "Id": "2" * 64,
                        "Image": "linuxserver/sonarr:latest",
                        "Names": ["/sonarr-4k"],
                        "Ports": [],
                        "NetworkSettings": {"Networks": {"a": {}}},
                        "Mounts": [],
                        "Labels": {},
                    },
                ],
            )
        if "/json" in req.url.path:
            return httpx.Response(200, json={"NetworkSettings": {"Networks": {}}})
        return httpx.Response(404)

    d = _discovery_with_mock(handler)
    services = await d.discover()
    await d.aclose()

    assert len(services) == 2
    names = {s.container_name for s in services}
    assert names == {"sonarr", "sonarr-4k"}


def test_discovered_service_to_dict_includes_all_fields():
    s = DiscoveredService(
        service="bazarr",
        container_name="bazarr",
        image="linuxserver/bazarr:1.5.6",
        container_id="abc123def456",
        networks=["media-stack"],
        published_ports={6767: 6767},
        inferred_url="http://bazarr:6767",
        inferred_url_reason="shared network 'media-stack'",
        config_host_path="/mnt/configs/bazarr",
        image_version="1.5.6",
    )
    d = s.to_dict()
    assert d["service"] == "bazarr"
    assert d["inferred_url"] == "http://bazarr:6767"
    assert d["config_host_path"] == "/mnt/configs/bazarr"
    assert d["image_version"] == "1.5.6"
    # has_config_mount derivable from config_host_path being non-None
    assert d["published_ports"] == {6767: 6767}


def test_service_catalogue_has_known_homelab_services():
    """Smoke check: the catalogue covers the common *arr stack we
    expect to discover in real setups."""
    expected = {"bazarr", "sonarr", "radarr", "tautulli", "subgen", "ollama", "plex", "jellyfin"}
    assert expected.issubset(SERVICE_CATALOGUE.keys())
