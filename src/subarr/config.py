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
    )


settings = load()
