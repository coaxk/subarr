"""Tier-2 docker discovery — read-only inspection via the docker API.

Talks to the docker daemon over HTTP. Two transports supported:

  - **tecnativa/docker-socket-proxy** at `tcp://docker-proxy:2375` (RECOMMENDED)
    — exposes a curated subset of the docker API. Subarr only needs
    GET /containers/json, GET /containers/{id}/json, GET /networks,
    GET /images/{id}/json. Configure the proxy with CONTAINERS=1
    NETWORKS=1 IMAGES=1 INFO=1 and EVERYTHING ELSE=0.

  - **Raw /var/run/docker.sock** (works, less safe) — bind-mounted ro
    into subarr's container. httpx supports the unix-socket transport
    via `transport=httpx.HTTPTransport(uds=...)`.

Detection rule: image-name regex + canonical container-name match,
fall back to image-name alone. Returns structured candidates per
service so the onboarding wizard can pre-fill URLs + offer Tier 3
config-mount setup.

Scope is intentionally narrow:
  - Read-only — never POSTs, never DELETEs, never EXECs
  - No user input flows into URL paths
  - Failures are best-effort — discovery is a convenience, not a
    correctness path

When the docker daemon is unreachable, discovery returns an empty
list and the wizard falls back to manual entry. The whole module
is opt-in: subarr boots fine without it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import settings

log = logging.getLogger(__name__)


# ─── Service catalogue ──────────────────────────────────────────────
#
# Image-name regex patterns the wizard recognises. First match wins.
# Internal ports are what the service listens on INSIDE the container;
# the wizard prefers these (joined to a shared network) over published
# host ports because they don't depend on the user's port mapping.

SERVICE_CATALOGUE: dict[str, dict[str, Any]] = {
    "bazarr": {
        "patterns": [r"\bbazarr\b"],
        "internal_port": 6767,
        "ws_pretty": "Bazarr",
    },
    "sonarr": {
        "patterns": [r"\bsonarr\b"],
        "internal_port": 8989,
        "ws_pretty": "Sonarr",
    },
    "radarr": {
        "patterns": [r"\bradarr\b"],
        "internal_port": 7878,
        "ws_pretty": "Radarr",
    },
    "tautulli": {
        "patterns": [r"\btautulli\b"],
        "internal_port": 8181,
        "ws_pretty": "Tautulli",
    },
    "subgen": {
        # subarr-subgen image first; vanilla subgen second. Both are
        # treated as the same logical service from subarr's POV.
        "patterns": [r"subarr-subgen", r"mccloud[s]?/subgen", r"\bsubgen\b"],
        "internal_port": 9000,
        "ws_pretty": "subgen",
    },
    "ollama": {
        "patterns": [r"\bollama\b"],
        "internal_port": 11434,
        "ws_pretty": "Ollama",
    },
    "plex": {
        "patterns": [r"plexinc/pms-docker", r"plex/plex", r"linuxserver/plex"],
        "internal_port": 32400,
        "ws_pretty": "Plex",
    },
    "jellyfin": {
        "patterns": [r"\bjellyfin\b"],
        "internal_port": 8096,
        "ws_pretty": "Jellyfin",
    },
}


@dataclass
class DiscoveredService:
    """One candidate found by discovery. The wizard chooses among these."""
    service: str                     # 'bazarr', 'sonarr', etc.
    container_name: str
    image: str
    container_id: str                # short ID (12 chars)
    networks: list[str] = field(default_factory=list)
    # Container's port → host port mapping (e.g. {6767: 6767, 8989: 8990}).
    # Empty when only attached to internal networks.
    published_ports: dict[int, int] = field(default_factory=dict)
    # Best-guess URL subarr should use to reach this service. Computed
    # by _infer_url() and depends on shared-network detection.
    inferred_url: str | None = None
    inferred_url_reason: str | None = None
    # Tier-3 hint: where does this container mount its /config dir on
    # the host? The wizard offers to volume-mount that path into subarr
    # read-only so API-key auto-extract can work.
    config_host_path: str | None = None
    # Image version label if upstream sets one (e.g. linuxserver/* uses
    # 'org.opencontainers.image.version').
    image_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "container_name": self.container_name,
            "image": self.image,
            "container_id": self.container_id,
            "networks": self.networks,
            "published_ports": self.published_ports,
            "inferred_url": self.inferred_url,
            "inferred_url_reason": self.inferred_url_reason,
            "config_host_path": self.config_host_path,
            "image_version": self.image_version,
        }


class DockerDiscovery:
    """Reads docker-API state to identify *arr stack services."""

    def __init__(
        self,
        base_url: str | None = None,
        unix_socket: str | None = None,
        timeout: float = 5.0,
    ):
        """Either base_url (e.g. http://docker-proxy:2375) OR
        unix_socket (e.g. /var/run/docker.sock) must be supplied. If
        neither, falls back to settings.docker_proxy_url and then
        /var/run/docker.sock."""
        if base_url:
            self._client = httpx.AsyncClient(
                base_url=base_url.rstrip("/"),
                timeout=timeout,
            )
            self._transport_kind = "proxy"
            self._endpoint = base_url
        elif unix_socket:
            transport = httpx.AsyncHTTPTransport(uds=unix_socket)
            self._client = httpx.AsyncClient(
                base_url="http://docker",
                transport=transport,
                timeout=timeout,
            )
            self._transport_kind = "socket"
            self._endpoint = unix_socket
        else:
            # Try settings.docker_proxy_url, then fall back to the raw
            # socket at the conventional path.
            url = getattr(settings, "docker_proxy_url", None)
            if url:
                self._client = httpx.AsyncClient(base_url=url.rstrip("/"), timeout=timeout)
                self._transport_kind = "proxy"
                self._endpoint = url
            else:
                transport = httpx.AsyncHTTPTransport(uds="/var/run/docker.sock")
                self._client = httpx.AsyncClient(
                    base_url="http://docker",
                    transport=transport,
                    timeout=timeout,
                )
                self._transport_kind = "socket"
                self._endpoint = "/var/run/docker.sock"

    async def aclose(self) -> None:
        await self._client.aclose()

    # ─── Public API ──────────────────────────────────────────────────

    async def reachable(self) -> bool:
        """Cheap liveness check. Used by the wizard to decide whether to
        offer auto-detect."""
        try:
            r = await self._client.get("/_ping")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def discover(self) -> list[DiscoveredService]:
        """Return all matched *arr services on the host.

        Walks all containers (running + stopped — stopped still gives us
        URL inference). For each, runs the service-catalogue matcher and
        builds a DiscoveredService with inferred URL + config mount hint.
        """
        try:
            containers = await self._list_containers(all_states=True)
        except httpx.HTTPError as e:
            log.warning("docker discovery: list containers failed: %s", e)
            return []
        if not containers:
            return []

        subarr_networks = await self._our_networks()
        results: list[DiscoveredService] = []
        for c in containers:
            service = self._match_service(c)
            if not service:
                continue
            ds = self._hydrate(service, c, subarr_networks)
            results.append(ds)
        return results

    async def discover_for_service(self, service: str) -> list[DiscoveredService]:
        """Filtered version of discover() — useful when the wizard is
        re-checking a specific integration."""
        all_ = await self.discover()
        return [d for d in all_ if d.service == service]

    # ─── Internal ────────────────────────────────────────────────────

    async def _list_containers(self, all_states: bool) -> list[dict]:
        params = {"all": "true"} if all_states else {}
        r = await self._client.get("/containers/json", params=params)
        r.raise_for_status()
        return r.json()

    async def _our_networks(self) -> set[str]:
        """Detect which networks subarr itself is on (for shared-network
        URL inference). Looks up the subarr container by hostname env
        var, falls back to the container ID via /etc/hostname inside the
        running process."""
        # When subarr runs in docker, the hostname is the container's
        # short ID. We use that to look ourselves up.
        try:
            import socket
            self_id = socket.gethostname()
        except Exception:
            return set()
        try:
            r = await self._client.get(f"/containers/{self_id}/json")
            if r.status_code != 200:
                return set()
            attrs = r.json()
            nets = (attrs.get("NetworkSettings") or {}).get("Networks") or {}
            return set(nets.keys())
        except httpx.HTTPError:
            return set()

    @staticmethod
    def _match_service(container: dict) -> str | None:
        image = container.get("Image", "") or ""
        names = container.get("Names", []) or []
        # Names look like ['/bazarr'] — strip leading slash for matching.
        clean_names = [n.lstrip("/") for n in names]
        lowered_image = image.lower()
        for service, spec in SERVICE_CATALOGUE.items():
            for pattern in spec["patterns"]:
                if re.search(pattern, lowered_image, flags=re.IGNORECASE):
                    return service
            # Also match canonical container names like 'bazarr', 'sonarr',
            # 'subarr-subgen' — catches images that don't include the
            # service name in their tag (e.g. private rebuilds).
            for name in clean_names:
                if name.lower() == service:
                    return service
        return None

    def _hydrate(
        self, service: str, container: dict, subarr_networks: set[str],
    ) -> DiscoveredService:
        names = container.get("Names", []) or []
        primary_name = names[0].lstrip("/") if names else "(unnamed)"
        image = container.get("Image", "") or ""
        cid = (container.get("Id") or "")[:12]
        nets_raw = container.get("NetworkSettings", {}).get("Networks", {}) or {}
        networks = list(nets_raw.keys())

        # Published ports — Docker returns these as a list of dicts with
        # PrivatePort + PublicPort.
        published: dict[int, int] = {}
        for p in (container.get("Ports") or []):
            priv = p.get("PrivatePort")
            pub = p.get("PublicPort")
            if priv and pub:
                published[priv] = pub

        # Image version label (linuxserver / many others use OCI labels).
        labels = container.get("Labels") or {}
        image_version = (
            labels.get("org.opencontainers.image.version")
            or labels.get("build_version")
            or None
        )

        # Config mount detection for Tier-3 hint. Match volume Source
        # paths whose Destination is /config (LinuxServer convention).
        # Container-list response includes Mounts; if not present we'd
        # need a follow-up /containers/{id}/json — skipping for now to
        # keep this single-call.
        config_host_path: str | None = None
        for m in (container.get("Mounts") or []):
            if m.get("Destination") == "/config":
                src = m.get("Source")
                if src:
                    config_host_path = src
                    break

        # URL inference.
        url, reason = self._infer_url(
            service, primary_name, networks, published, subarr_networks,
        )

        return DiscoveredService(
            service=service,
            container_name=primary_name,
            image=image,
            container_id=cid,
            networks=networks,
            published_ports=published,
            inferred_url=url,
            inferred_url_reason=reason,
            config_host_path=config_host_path,
            image_version=image_version,
        )

    @staticmethod
    def _infer_url(
        service: str, container_name: str, networks: list[str],
        published: dict[int, int], subarr_networks: set[str],
    ) -> tuple[str | None, str | None]:
        """Best-guess URL for subarr to reach this service.

        Preference order:
          1. SHARED NETWORK — http://{container-name}:{internal-port}.
             This is the gold path: stays inside Docker, no port
             forwarding needed, fastest.
          2. PUBLISHED HOST PORT — http://host.docker.internal:{host-port}
             when running on Docker Desktop, OR a documented host-IP
             entry in the wizard. The internal port is mapped out so
             we use the host port the user chose.
          3. Nothing usable — return None and the wizard surfaces a
             "couldn't infer URL" hint, user types manually.
        """
        spec = SERVICE_CATALOGUE.get(service, {})
        internal_port = spec.get("internal_port")
        if not internal_port:
            return None, None

        shared = set(networks) & subarr_networks
        if shared:
            net = sorted(shared)[0]
            return (
                f"http://{container_name}:{internal_port}",
                f"shared network '{net}'",
            )

        if internal_port in published:
            host_port = published[internal_port]
            return (
                f"http://host.docker.internal:{host_port}",
                f"published port {host_port}",
            )

        # Nothing reachable from subarr's POV without manual config.
        return None, (
            f"on network(s) {networks!r} which subarr isn't on; "
            f"no port {internal_port} published to host"
        )
