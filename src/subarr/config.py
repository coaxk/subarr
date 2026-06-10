"""Runtime config. Env-driven so dev (host) and prod (container) both work.

Env var prefix is SUBARR_*. SUBGEN_* is reserved for things specifically about
the subgen container (URL, container name, compose path).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from .libraries import Library, LibraryConfigError, build_libraries

log = logging.getLogger(__name__)


def _env_or(name: str, default: str) -> str:
    """Read an env var, treating empty / whitespace-only as missing.

    Standard `os.environ.get(name, default)` only returns the default when
    the key is ABSENT; if the user has the var declared but empty (e.g.
    `BAZARR_URL=` in a .env file, a common docker-compose pattern when a
    user wants to comment-out without deleting the line), .get() returns
    "" and the configured default silently never applies.

    #127: This used to land empty strings in fields where empty is
    semantically meaningless (subgen_url="" → every /batch call 502s with
    no diagnosable reason). Use this helper for any field whose default
    is the only valid resting value.

    Do NOT use this for fields where empty IS the off-signal (API keys,
    telemetry_endpoint, auth_user, auth_pass, optional discovery URLs).
    Those keep bare .get() so the user can disable by clearing the value.
    """
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw


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
    # v1.1.1: path Plex sees the media tree at, when different from subarr's
    # media_root. Used by partial-scan to translate sidecar paths to Plex's
    # view before issuing /library/sections/{id}/refresh?path=. Leave empty
    # when both containers mount the same path (common case).
    plex_path_prefix: str
    # v1.1.1: master toggle for partial-scan-on-sidecar-write. Default on;
    # set PLEX_PARTIAL_SCAN_ENABLED=0 to fall back to whatever scan cadence
    # Plex's own scheduler runs at.
    plex_partial_scan_enabled: bool
    # #87: accept subgen's WEBHOOK_URL_COMPLETED push at
    # POST /api/subgen/webhook/completed. Push beats polling — lower
    # queue-UI latency, fewer requests. Default on; the polling watcher
    # stays running as the fallback for vanilla subgen (operators who
    # haven't pointed WEBHOOK_URL_COMPLETED at subarr). Set
    # SUBARR_SUBGEN_WEBHOOK_ENABLED=0 to reject pushes and rely on polling.
    subgen_webhook_enabled: bool
    # v1.1.1 #219 closer: PUT user-verified audio language back to Sonarr's
    # episodeFile so Bazarr's next sync sees the correct foreign-language
    # audio and unblinds itself. Writes to Sonarr's DB, so OPT-IN. Default
    # off; set SONARR_PROPAGATE_AUDIO_LANG=1 to enable.
    sonarr_propagate_audio_lang: bool
    # #12: read the user's per-show selected audio language directly from
    # Plex metadata (funnel layer L2.6, below Tautulli-live). Adds Plex API
    # calls per coverage build, so OPT-IN. Default off; set
    # PLEX_AUDIO_HINTS=1 to enable.
    plex_audio_hints: bool

    # #111: speech-aware audio analysis via silero VAD. The onnxruntime+numpy
    # runtime ships in the image; the ~2MB model is pulled on opt-in. Master
    # switch (default on, "recommended"): even when on, the VAD path only runs
    # once the model is present (vad.vad_available()), so a fresh install never
    # surprise-downloads — it falls back to silencedetect until the user pulls
    # the model from onboarding. Set SUBARR_VAD_ENABLED=0 to hard-disable.
    vad_enabled: bool

    # #104: minimum seconds between coverage-cache rebuilds triggered by
    # event kicks (completion / audio-lang verify / manual refresh). A
    # burst of events coalesces into a single rebuild, and rebuilds are
    # spaced at least this far apart so we don't hammer the NAS + thread
    # pool. The fixed background loop (DEFAULT_INTERVAL_S) is the floor
    # cadence; this knob debounces the on-demand kicks on top of it.
    coverage_refresh_min_interval_s: float

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
    # #232: Separate vision-capable model for v1.1-K (Tautulli thumb
    # classification — hardcoded subs / dialog density). Text-only
    # models like qwen2.5:7b cannot process images; calling them with
    # `images=[...]` returns garbage. Subarr now detects whether the
    # configured vision model is installed and gracefully disables the
    # vision pre-filter when it is not — every other ollama feature
    # keeps working with the text model. Set to "auto" to let subarr
    # pick the first vision-capable model from /api/tags.
    ollama_vision_model: str

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
    # https://telemetry.subarr.com/v1/ping when we publish that worker.
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
    # #133: per-service path prefixes. Most homelabs have Sonarr at /data/TV/
    # and Radarr at /data/Movies/ — one shared prefix forced users into a
    # useless common parent. Both fall back to arr_path_prefix when their
    # own env var is unset so existing deployments keep working unchanged.
    sonarr_path_prefix: str
    radarr_path_prefix: str

    # #136: age-based retention for arena_runs (tuning-lab sweeps). The table is
    # append-only and grows unbounded on long-running installs; sweeps older than
    # this many days are pruned on boot. Default 30 (not pending_store's 7) because
    # sweeps are durable history feeding the federated tournament (#124) — pruning
    # too aggressively destroys that crowd-curated signal. 0/negative disables
    # pruning (keep everything). Set SUBARR_ARENA_RETENTION_DAYS to override.
    arena_retention_days: int

    # #134 Phase 1: the validated library list. Library 0 (slug "") mirrors
    # the legacy media_root / subgen_media_prefix / arr_path_prefix scalars
    # for back-compat; additional libraries come from the persisted override
    # store. Built in load() after the scalars + overrides are resolved.
    # Tuple (not list) because Settings is frozen.
    libraries: tuple[Library, ...] = ()


def load() -> Settings:
    # See _env_or docstring for the empty-string fall-through rule (#127).
    # Helper applied where empty is semantically meaningless. Bare .get() kept
    # for fields where empty IS the intended off-signal (API keys, telemetry,
    # auth, optional docker discovery).
    _s = Settings(
        media_root=Path(_env_or("SUBARR_MEDIA_ROOT", "/media/library")),
        subgen_compose_path=Path(_env_or("SUBGEN_COMPOSE_PATH", "/dockercontainers/subgen/compose.yaml")),
        subgen_url=_env_or("SUBGEN_URL", "http://subgen:9000"),
        subgen_container=_env_or("SUBGEN_CONTAINER", "subgen"),
        subgen_media_prefix=_env_or("SUBGEN_MEDIA_PREFIX", "/media"),
        db_path=Path(_env_or("SUBARR_DB_PATH", "/data/subarr.db")),
        port=int(_env_or("SUBARR_PORT", "9922")),
        plex_url=_env_or("PLEX_URL", "http://192.168.1.105:32400"),
        plex_token=os.environ.get("PLEX_TOKEN", ""),
        plex_section=_env_or("PLEX_SECTION", "all"),
        plex_path_prefix=os.environ.get("PLEX_PATH_PREFIX", ""),
        plex_partial_scan_enabled=_env_or("PLEX_PARTIAL_SCAN_ENABLED", "1").strip().lower()
        not in ("0", "false", "no", "off"),
        subgen_webhook_enabled=_env_or("SUBARR_SUBGEN_WEBHOOK_ENABLED", "1").strip().lower()
        not in ("0", "false", "no", "off"),
        sonarr_propagate_audio_lang=os.environ.get("SONARR_PROPAGATE_AUDIO_LANG", "0").strip().lower()
        in ("1", "true", "yes", "on"),
        plex_audio_hints=os.environ.get("PLEX_AUDIO_HINTS", "0").strip().lower()
        in ("1", "true", "yes", "on"),
        vad_enabled=_env_or("SUBARR_VAD_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off"),
        # #104: default 120s. Min-clamped at 0 (a 0/negative value disables
        # debounce — every kick rebuilds, the pre-#104 behaviour).
        coverage_refresh_min_interval_s=max(
            0.0, float(_env_or("SUBARR_COVERAGE_REFRESH_MIN_INTERVAL_S", "120"))
        ),
        # Integration URLs use _env_or so a blank line in .env still gets the
        # sane in-cluster default. Disabling an integration is signalled by
        # the empty api_key, not by clearing the URL.
        bazarr_url=_env_or("BAZARR_URL", "http://bazarr:6767"),
        bazarr_api_key=os.environ.get("BAZARR_API_KEY", ""),
        sonarr_url=_env_or("SONARR_URL", "http://sonarr:8989"),
        sonarr_api_key=os.environ.get("SONARR_API_KEY", ""),
        radarr_url=_env_or("RADARR_URL", "http://radarr:7878"),
        radarr_api_key=os.environ.get("RADARR_API_KEY", ""),
        tautulli_url=_env_or("TAUTULLI_URL", "http://tautulli:8181"),
        tautulli_api_key=os.environ.get("TAUTULLI_API_KEY", ""),
        arr_path_prefix=_env_or("ARR_PATH_PREFIX", "/data/Media/"),
        # #133: each falls back to ARR_PATH_PREFIX if its own var is unset,
        # so users with the legacy single-prefix .env keep working until
        # they explicitly opt in to split prefixes.
        sonarr_path_prefix=_env_or(
            "SONARR_PATH_PREFIX",
            _env_or("ARR_PATH_PREFIX", "/data/TV/"),
        ),
        radarr_path_prefix=_env_or(
            "RADARR_PATH_PREFIX",
            _env_or("ARR_PATH_PREFIX", "/data/Movies/"),
        ),
        ollama_url=_env_or("OLLAMA_URL", "http://ollama:11434"),
        ollama_model=_env_or("OLLAMA_MODEL", "qwen2.5:7b"),
        # #232: defaults to qwen2.5vl:7b (the recommended pull). "auto"
        # = subarr picks the first vision-capable installed model.
        ollama_vision_model=_env_or("OLLAMA_VISION_MODEL", "qwen2.5vl:7b"),
        docker_proxy_url=os.environ.get("SUBARR_DOCKER_PROXY_URL", ""),
        docker_socket_path=os.environ.get("SUBARR_DOCKER_SOCKET_PATH", ""),
        # telemetry_endpoint keeps bare .get(): empty = "don't transmit",
        # the user's explicit off-switch. Don't fall through to the default.
        telemetry_endpoint=os.environ.get(
            "SUBARR_TELEMETRY_ENDPOINT",
            "https://telemetry.subarr.com/v1/ping",
        ),
        auth_user=os.environ.get("SUBARR_USER", ""),
        auth_pass=os.environ.get("SUBARR_PASS", ""),
        # #136: default 30 days. 0/negative disables arena-run pruning.
        arena_retention_days=int(_env_or("SUBARR_ARENA_RETENTION_DAYS", "30")),
    )
    _apply_persisted_overrides(_s)

    # #134 Phase 1: build the library list. Library 0 = the legacy scalars
    # (which _apply_persisted_overrides may have adjusted). Extras come from
    # the override store's "libraries" key. Fail-soft: any config error logs
    # and degrades to the single default library so boot never breaks.
    _default_lib = Library(
        slug="",
        name="default",
        fs_root=_s.media_root,
        subgen_prefix=_s.subgen_media_prefix,
        arr_prefix=_s.arr_path_prefix,
    )
    try:
        from . import config_store

        _raw_extras = config_store.load_overrides().get("libraries", [])
        if not isinstance(_raw_extras, list):
            _raw_extras = []
        _libs = build_libraries(_default_lib, _raw_extras)
    except LibraryConfigError:
        log.warning("invalid libraries[] config; using single default library", exc_info=True)
        _libs = (_default_lib,)
    object.__setattr__(_s, "libraries", _libs)
    return _s


# ─── Onboarding clobber guard support ───────────────────────────────
# Maps each Settings field the onboarding wizard can write to the env var
# that backs it in load(). config owns this so the field<->env relationship
# has a single source of truth (drift-checked in tests). Used by the
# onboarding apply step: an env-set field is the operator's authoritative
# declaration and must NOT be overwritten by stored wizard progress.
FIELD_ENV_VARS: dict[str, str] = {
    "media_root": "SUBARR_MEDIA_ROOT",
    "arr_path_prefix": "ARR_PATH_PREFIX",
    "bazarr_url": "BAZARR_URL",
    "bazarr_api_key": "BAZARR_API_KEY",
    "sonarr_url": "SONARR_URL",
    "sonarr_api_key": "SONARR_API_KEY",
    "radarr_url": "RADARR_URL",
    "radarr_api_key": "RADARR_API_KEY",
    "tautulli_url": "TAUTULLI_URL",
    "tautulli_api_key": "TAUTULLI_API_KEY",
    "subgen_url": "SUBGEN_URL",
    "ollama_url": "OLLAMA_URL",
    "ollama_model": "OLLAMA_MODEL",
    # #75: Plex creds become UI-editable. PLEX_URL has a built-in default
    # (via _env_or) but env_is_set() checks the raw env var presence, so an
    # operator who pinned PLEX_URL keeps authority; a default-only install
    # is treated as unset and the UI write is honoured.
    "plex_url": "PLEX_URL",
    "plex_token": "PLEX_TOKEN",
    # #111/#112: UI-settable toggles. Listed here so env_is_set() lets an
    # explicit env var override a persisted UI choice (env > file > default).
    "vad_enabled": "SUBARR_VAD_ENABLED",
    "plex_audio_hints": "PLEX_AUDIO_HINTS",
    "sonarr_propagate_audio_lang": "SONARR_PROPAGATE_AUDIO_LANG",
    "plex_partial_scan_enabled": "PLEX_PARTIAL_SCAN_ENABLED",
    "subgen_webhook_enabled": "SUBARR_SUBGEN_WEBHOOK_ENABLED",
}


def env_is_set(settings_attr: str) -> bool:
    """True iff the env var backing this Settings field is present AND
    non-empty (mirrors _env_or's empty==missing rule, so a blank
    `BAZARR_URL=` line counts as unset)."""
    name = FIELD_ENV_VARS.get(settings_attr)
    if not name:
        return False
    raw = os.environ.get(name)
    return raw is not None and raw.strip() != ""


def _coerce_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() not in ("0", "false", "no", "off", "")


# #112: per-field coercion for persisted overrides (JSON → typed). Only
# fields listed here can be set from the UI persistence layer; everything
# else in an override file is ignored (defensive — the file never widens the
# config surface beyond what we intend to be user-settable).
_FIELD_COERCE = {
    "vad_enabled": _coerce_bool,
    "plex_audio_hints": _coerce_bool,
    "sonarr_propagate_audio_lang": _coerce_bool,
    "plex_partial_scan_enabled": _coerce_bool,
    "subgen_webhook_enabled": _coerce_bool,
    "ollama_model": str,
    "ollama_url": str,
    "ollama_vision_model": str,
    # #75: integration credentials are now UI-editable. Persisting them
    # here means a saved URL / API key / Plex token survives a restart
    # (env still wins per _apply_persisted_overrides). All plain strings.
    "bazarr_url": str,
    "bazarr_api_key": str,
    "sonarr_url": str,
    "sonarr_api_key": str,
    "radarr_url": str,
    "radarr_api_key": str,
    "tautulli_url": str,
    "tautulli_api_key": str,
    "plex_url": str,
    "plex_token": str,
    "subgen_url": str,
}


def _apply_persisted_overrides(s: Settings) -> None:
    """Overlay persisted UI overrides onto Settings, BELOW env (env wins).
    Frozen dataclass → object.__setattr__. Fail-soft per field so one bad
    value can't break config load."""
    from . import config_store

    for field, raw in config_store.load_overrides().items():
        coerce = _FIELD_COERCE.get(field)
        if coerce is None:
            continue  # not a UI-settable field; ignore
        if env_is_set(field):
            continue  # operator's env is authoritative
        try:
            object.__setattr__(s, field, coerce(raw))
        except Exception:
            log.warning("override %s=%r failed to apply", field, raw, exc_info=True)


settings = load()
