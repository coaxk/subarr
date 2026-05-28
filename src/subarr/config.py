"""Runtime config. Env-driven so dev (host) and prod (container) both work.

Env var prefix is SUBARR_*. SUBGEN_* is reserved for things specifically about
the subgen container (URL, container name, compose path).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    # Root of the media library the folder tree browses. Inside container: /media/library.
    # On dev host: point at Z:/Media/TV (or wherever). Browse paths are canonical: relative to this.
    media_root: Path

    # Path to subgen's compose.yaml — read for transparency view of per-language kwargs.
    # Mounted read-only in production; Subarr never writes here.
    subgen_compose_path: Path

    # HTTP base for subgen's API (in-cluster: http://subgen:9000; dev: http://localhost:9007).
    subgen_url: str

    # Subgen container name (for docker logs/restart).
    subgen_container: str

    # Filesystem prefix subgen sees library files under. Subgen's compose
    # mounts /mnt/nas/Media:/media so canonical paths map to /media/<canonical>.
    # PS V69's working /batch calls use this same prefix.
    subgen_media_prefix: str

    # SQLite path for scan queue persistence.
    db_path: Path

    # Port the GUI listens on.
    port: int

    # Plex library refresh target. URL is the LAN-direct PMS; token is from
    # the user's PMS settings. Section ID 0 means "all libraries" (Plex spec).
    plex_url: str
    plex_token: str
    plex_section: str  # "all" or numeric section ID

    # v1.1 Coverage dashboard integrations. Empty url disables the upstream.
    bazarr_url: str
    bazarr_api_key: str
    sonarr_url: str
    sonarr_api_key: str
    radarr_url: str
    radarr_api_key: str
    tautulli_url: str
    tautulli_api_key: str

    # Ollama LLM endpoint for v1.2 enrichment (originalLanguage inference
    # for rows where Sonarr returned null/und).
    ollama_url: str
    ollama_model: str

    # Docker discovery (Tier-2 read-only introspection) — optional. When
    # set, the onboarding wizard pre-fills integration URLs by reading
    # docker container metadata. RECOMMENDED form is the tecnativa
    # docker-socket-proxy with CONTAINERS+NETWORKS+IMAGES+INFO scopes
    # only; raw /var/run/docker.sock works but exposes more API surface.
    # Empty disables auto-discovery; wizard falls back to manual entry.
    docker_proxy_url: str
    docker_socket_path: str

    # Telemetry endpoint. When empty, telemetry is collected locally
    # (visible in Settings) but never transmitted. Set to e.g.
    # https://telemetry.subarr.dev/v1/ping when we publish that worker.
    telemetry_endpoint: str

    # Optional HTTP Basic auth. When BOTH SUBARR_USER and SUBARR_PASS
    # are set, every non-allowlisted request requires creds. When
    # unset (default), no auth. Recommended production posture is a
    # reverse proxy with proper auth (Authelia, Caddy basicauth, etc.);
    # this is the in-product fallback for users who can't put subarr
    # behind a proxy.
    auth_user: str
    auth_pass: str

    # Filesystem prefix subgen prepends to canonical paths inside its container.
    # /api/coverage uses this to map a Sonarr/Radarr `path` field back to the
    # canonical-to-subarr form used everywhere else (relative to media_root).
    # Subgen sees Sonarr/Radarr paths as /data/Media/<...>; Subarr sees the
    # same files at /media/library/<...>. This prefix is what Sonarr/Radarr
    # store as `path`; we strip it to canonicalise.
    arr_path_prefix: str


def load() -> Settings:
    return Settings(
        media_root=Path(os.environ.get("SUBARR_MEDIA_ROOT", "/media/library")),
        subgen_compose_path=Path(
            os.environ.get("SUBGEN_COMPOSE_PATH", "/dockercontainers/subgen/compose.yaml")
        ),
        subgen_url=os.environ.get("SUBGEN_URL", "http://subgen:9000"),
        subgen_container=os.environ.get("SUBGEN_CONTAINER", "subgen"),
        subgen_media_prefix=os.environ.get("SUBGEN_MEDIA_PREFIX", "/media"),
        db_path=Path(os.environ.get("SUBARR_DB_PATH", "/data/subarr.db")),
        port=int(os.environ.get("SUBARR_PORT", "9922")),
        plex_url=os.environ.get("PLEX_URL", "http://192.168.1.105:32400"),
        plex_token=os.environ.get("PLEX_TOKEN", ""),
        plex_section=os.environ.get("PLEX_SECTION", "all"),
        bazarr_url=os.environ.get("BAZARR_URL", "http://bazarr:6767"),
        bazarr_api_key=os.environ.get("BAZARR_API_KEY", ""),
        sonarr_url=os.environ.get("SONARR_URL", "http://sonarr:8989"),
        sonarr_api_key=os.environ.get("SONARR_API_KEY", ""),
        radarr_url=os.environ.get("RADARR_URL", "http://radarr:7878"),
        radarr_api_key=os.environ.get("RADARR_API_KEY", ""),
        tautulli_url=os.environ.get("TAUTULLI_URL", "http://tautulli:8181"),
        tautulli_api_key=os.environ.get("TAUTULLI_API_KEY", ""),
        arr_path_prefix=os.environ.get("ARR_PATH_PREFIX", "/data/Media/"),
        ollama_url=os.environ.get("OLLAMA_URL", "http://ollama:11434"),
        ollama_model=os.environ.get("OLLAMA_MODEL", "qwen2.5:7b"),
        docker_proxy_url=os.environ.get("SUBARR_DOCKER_PROXY_URL", ""),
        docker_socket_path=os.environ.get("SUBARR_DOCKER_SOCKET_PATH", ""),
        telemetry_endpoint=os.environ.get("SUBARR_TELEMETRY_ENDPOINT", ""),
        auth_user=os.environ.get("SUBARR_USER", ""),
        auth_pass=os.environ.get("SUBARR_PASS", ""),
    )


settings = load()
