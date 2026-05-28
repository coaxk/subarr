"""Tier-3 API-key auto-extract from *arr config files.

For each integration (bazarr, sonarr, radarr, tautulli), tries a chain
of progressively-more-permissive sources to find an API key, in order
of permission cost:

  1. **Subarr's own env vars** (BAZARR_API_KEY etc.) — no permission
     needed. Power-user shortcut: put keys in subarr's own .env, the
     wizard becomes a confirmation screen.
  2. **Target container's docker-inspect env** — requires Tier-2 docker
     access. Catches the "Bazarr's compose templates `${BAZARR_API_KEY}`
     from .env" pattern; docker resolves to literal value at runtime.
  3. **Mounted *arr config file** — config.yaml (Bazarr), config.xml
     (Sonarr/Radarr), config.ini (Tautulli). The canonical source of
     truth for what the *arr app actually uses.
  4. **.env file alongside the mounted config** — catches the templated-
     config setups where .env is the source of truth and config.yaml
     is regenerated.

First match wins. The result carries which source it came from so the
UI can surface that transparency ("API key detected from container
env" vs "from config.yaml"). When two sources have *different* values
we surface the conflict — never silently pick.

Read-only throughout. Never writes to *arr config files. Never escapes
the mounted dir.
"""

from __future__ import annotations

import configparser
import logging
import os
import re
import defusedxml.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ─── Service → file/key/parser mapping ───────────────────────────────


@dataclass(frozen=True)
class ServiceConfigSpec:
    """How to find + parse one *arr service's API key."""
    service: str
    config_filename: str
    parser: str                  # 'yaml-apikey' | 'xml-element' | 'ini-key'
    env_var_name: str            # e.g. 'BAZARR_API_KEY' — used by source 1 + 2


SERVICE_CONFIGS: dict[str, ServiceConfigSpec] = {
    "bazarr": ServiceConfigSpec(
        service="bazarr",
        config_filename="config.yaml",
        parser="yaml-apikey",
        env_var_name="BAZARR_API_KEY",
    ),
    "sonarr": ServiceConfigSpec(
        service="sonarr",
        config_filename="config.xml",
        parser="xml-element",
        env_var_name="SONARR_API_KEY",
    ),
    "radarr": ServiceConfigSpec(
        service="radarr",
        config_filename="config.xml",
        parser="xml-element",
        env_var_name="RADARR_API_KEY",
    ),
    "tautulli": ServiceConfigSpec(
        service="tautulli",
        config_filename="config.ini",
        parser="ini-key",
        env_var_name="TAUTULLI_API_KEY",
    ),
}


# ─── Result types ────────────────────────────────────────────────────


@dataclass
class ExtractedKey:
    """One discovered API key + provenance info for the UI."""
    service: str
    api_key: str
    source: str                  # human-readable: 'subarr env' / 'docker env' / 'config file' / '.env file'
    source_detail: str | None = None  # e.g. file path or env var name

    @property
    def masked(self) -> str:
        """For UI display: '••••f6c0' style mask."""
        if not self.api_key:
            return ""
        if len(self.api_key) <= 4:
            return "••••"
        return "••••" + self.api_key[-4:]

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "masked_key": self.masked,
            "source": self.source,
            "source_detail": self.source_detail,
        }


@dataclass
class ExtractResult:
    """Result of trying to extract one service's key.

    `key` is the chosen value (or None). `candidates` is all the
    sources that found a key — usually one, sometimes more if multiple
    sources disagree (UI shows the conflict)."""
    service: str
    key: ExtractedKey | None
    candidates: list[ExtractedKey]
    conflict: bool             # true iff multiple candidates with different values

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "found": self.key is not None,
            "chosen": self.key.to_dict() if self.key else None,
            "candidates": [c.to_dict() for c in self.candidates],
            "conflict": self.conflict,
            "candidate_count": len(self.candidates),
        }


# ─── Per-source extractors ───────────────────────────────────────────


def _from_subarr_env(spec: ServiceConfigSpec, env: dict[str, str]) -> ExtractedKey | None:
    """Source 1: subarr's own env vars."""
    val = env.get(spec.env_var_name)
    if val:
        return ExtractedKey(
            service=spec.service,
            api_key=val,
            source="subarr env",
            source_detail=spec.env_var_name,
        )
    return None


def _from_container_env(
    spec: ServiceConfigSpec, container_env: list[str] | None,
) -> ExtractedKey | None:
    """Source 2: target container's resolved env vars.

    `container_env` is the list of KEY=VAL strings from docker inspect's
    Config.Env array. We look for the same env-var name (BAZARR_API_KEY
    on the bazarr container, etc.).
    """
    if not container_env:
        return None
    prefix = spec.env_var_name + "="
    for entry in container_env:
        if entry.startswith(prefix):
            val = entry[len(prefix):]
            if val:
                return ExtractedKey(
                    service=spec.service,
                    api_key=val,
                    source="docker env",
                    source_detail=f"{spec.env_var_name} on container",
                )
    return None


def _from_config_file(
    spec: ServiceConfigSpec, config_dir: Path | None,
) -> ExtractedKey | None:
    """Source 3: mounted *arr config file (the canonical source)."""
    if config_dir is None or not config_dir.is_dir():
        return None
    config_path = config_dir / spec.config_filename
    if not config_path.is_file():
        return None
    try:
        if spec.parser == "yaml-apikey":
            return _parse_bazarr_yaml(config_path, spec.service)
        if spec.parser == "xml-element":
            return _parse_sonarr_radarr_xml(config_path, spec.service)
        if spec.parser == "ini-key":
            return _parse_tautulli_ini(config_path, spec.service)
    except Exception as e:
        log.debug("config parse failed for %s at %s: %s", spec.service, config_path, e)
    return None


def _from_env_file(
    spec: ServiceConfigSpec, config_dir: Path | None,
) -> ExtractedKey | None:
    """Source 4: .env file in same dir as the mounted config."""
    if config_dir is None or not config_dir.is_dir():
        return None
    env_path = config_dir / ".env"
    if not env_path.is_file():
        return None
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key.strip() == spec.env_var_name:
                val = val.strip().strip('"').strip("'")
                if val:
                    return ExtractedKey(
                        service=spec.service,
                        api_key=val,
                        source=".env file",
                        source_detail=str(env_path),
                    )
    except Exception as e:
        log.debug(".env parse failed for %s at %s: %s", spec.service, env_path, e)
    return None


# ─── Per-format parsers ──────────────────────────────────────────────


def _parse_bazarr_yaml(path: Path, service: str) -> ExtractedKey | None:
    """Bazarr's config.yaml uses YAML. We avoid PyYAML to keep deps
    minimal — just grep for 'apikey:' since the file is small and the
    structure is well-known."""
    text = path.read_text(encoding="utf-8", errors="replace")
    # Look for 'apikey:' or 'apikey :' on its own line, value follows.
    # Bazarr's apikey lives under the 'auth' section but we accept any
    # top-level/indented match — the format is stable enough.
    for line in text.splitlines():
        m = re.match(r"^\s*apikey\s*:\s*(['\"]?)([0-9a-fA-F]{16,})\1\s*$", line)
        if m:
            return ExtractedKey(
                service=service, api_key=m.group(2),
                source="config file", source_detail=str(path),
            )
    return None


def _parse_sonarr_radarr_xml(path: Path, service: str) -> ExtractedKey | None:
    """Sonarr/Radarr use the same XML schema: <Config><ApiKey>...</ApiKey>"""
    tree = ET.parse(str(path))
    root = tree.getroot()
    for elem in root.iter():
        if elem.tag.lower() == "apikey" and elem.text:
            key = elem.text.strip()
            if key:
                return ExtractedKey(
                    service=service, api_key=key,
                    source="config file", source_detail=str(path),
                )
    return None


def _parse_tautulli_ini(path: Path, service: str) -> ExtractedKey | None:
    """Tautulli's config.ini has [General] api_key = ..."""
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(str(path), encoding="utf-8")
    for section in ("General", "general"):
        if cp.has_option(section, "api_key"):
            val = cp.get(section, "api_key").strip()
            if val:
                return ExtractedKey(
                    service=service, api_key=val,
                    source="config file", source_detail=str(path),
                )
    return None


# ─── Public API ──────────────────────────────────────────────────────


def extract_for_service(
    service: str,
    *,
    subarr_env: dict[str, str] | None = None,
    container_env: list[str] | None = None,
    config_dir: Path | None = None,
) -> ExtractResult:
    """Run the resolver chain for one service. First match wins.

    If multiple sources have *different* values, all are returned in
    `candidates` + conflict=True; the chosen value is the highest-
    permission source (3 > 4 > 1 > 2) since the actual config file
    is what the *arr app uses at runtime.
    """
    spec = SERVICE_CONFIGS.get(service)
    if spec is None:
        return ExtractResult(service=service, key=None, candidates=[], conflict=False)

    env = subarr_env if subarr_env is not None else dict(os.environ)

    candidates: list[ExtractedKey] = []
    for src_fn in (
        lambda: _from_subarr_env(spec, env),
        lambda: _from_container_env(spec, container_env),
        lambda: _from_config_file(spec, config_dir),
        lambda: _from_env_file(spec, config_dir),
    ):
        c = src_fn()
        if c is not None:
            candidates.append(c)

    if not candidates:
        return ExtractResult(service=service, key=None, candidates=[], conflict=False)

    distinct_values = {c.api_key for c in candidates}
    conflict = len(distinct_values) > 1
    chosen = _choose_authoritative(candidates)
    return ExtractResult(
        service=service, key=chosen, candidates=candidates, conflict=conflict,
    )


def _choose_authoritative(candidates: list[ExtractedKey]) -> ExtractedKey:
    """When sources conflict, prefer the one *arr actually uses.

    Priority: 'config file' > '.env file' > 'subarr env' > 'docker env'.

    Rationale: the config file is what the *arr app reads at boot —
    that's the de-facto truth. .env is what's likely going to BE the
    config after a regen. subarr env is documentation; docker env is
    docker-compose's interpretation. When in doubt, follow what the
    runtime actually uses.
    """
    priority = {"config file": 0, ".env file": 1, "subarr env": 2, "docker env": 3}
    return min(candidates, key=lambda c: priority.get(c.source, 99))


def extract_all(
    services: list[str] | None = None,
    *,
    config_dirs: dict[str, Path] | None = None,
    container_envs: dict[str, list[str]] | None = None,
    subarr_env: dict[str, str] | None = None,
) -> dict[str, ExtractResult]:
    """Resolver for all known services at once.

    `config_dirs[service]` → Path to /host-configs/<service>/ mount.
    `container_envs[service]` → docker-inspect Config.Env list.
    `subarr_env` → defaults to os.environ.
    """
    if services is None:
        services = list(SERVICE_CONFIGS.keys())
    return {
        s: extract_for_service(
            s,
            subarr_env=subarr_env,
            container_env=(container_envs or {}).get(s),
            config_dir=(config_dirs or {}).get(s),
        )
        for s in services
    }
