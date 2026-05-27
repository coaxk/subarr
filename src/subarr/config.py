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
    )


settings = load()
