"""Runtime config. Env-driven so dev (host) and prod (container) both work.

Env var prefix is SUBARR_*. SUBGEN_* is reserved for things specifically about
the subgen container (URL, container name, compose path).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from .instances import Instance, InstanceConfigError, build_instances
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


# #479: the SUBGEN_URL fallback, named once so config and telemetry cannot
# disagree about what "default" means. A duplicated literal here would make the
# telemetry silently report the wrong thing while everything else kept working.
DEFAULT_SUBGEN_URL = "http://subgen:9000"


def subgen_url_is_default(url: object) -> bool:
    """Is this install still on the shipped SUBGEN_URL?

    #479: 108 genuine installs report subgen unreachable and 104 of them logged
    no errors at all in 30 days. The default is `http://subgen:9000`, a hostname
    that resolves only inside one specific compose topology, so an install that
    never configured subgen probes a host that does not exist and reports
    identically to one whose real subgen went down. This boolean separates the
    two. It transmits no URL.

    Empty counts as default because that is what the app actually does: `_env_or`
    maps an empty SUBGEN_URL back to the fallback, so such an install genuinely
    IS running on the default. Reporting otherwise would make this field
    disagree with the behaviour it describes.

    Never raises. It runs inside the telemetry payload build, where an exception
    would take out the whole ping.
    """
    try:
        if url is None:
            return True
        if not isinstance(url, str):
            return False
        raw = url.strip()
        if not raw:
            return True
        return raw.rstrip("/").casefold() == DEFAULT_SUBGEN_URL.rstrip("/").casefold()
    except Exception:
        return False


def _normalize_samesite(v: str) -> str:
    """#238: session-cookie SameSite — lax (default) / strict / none. Anything
    unrecognized falls back to the safe `lax` rather than erroring at boot."""
    val = (v or "").strip().lower()
    return val if val in ("lax", "strict", "none") else "lax"


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
    # #71: Jellyfin media-server backend (fan-out alternative to Plex).
    # Empty by default — Jellyfin is unconfigured until an operator sets
    # JELLYFIN_URL/JELLYFIN_API_KEY (mirrors plex_token's off-by-default).
    jellyfin_url: str
    jellyfin_api_key: str
    # Filesystem prefix Jellyfin sees the media tree at, when different from
    # subarr's media_root. Mirrors plex_path_prefix. Leave empty when both
    # containers mount the same path (common case).
    jellyfin_path_prefix: str
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
    # #359: re-time finished .srt subtitles (extend over-CPS cues into gaps)
    # before aftercare/upload. Off by default until the params are arena-proven.
    retime_enabled: bool
    # #364: opt-in "Deep-scan English files for foreign scenes" — drives the
    # forced-segment walker + the at-import hook. OFF by default so the skip-
    # English optimisation is byte-for-byte unchanged for everyone who does not
    # opt in. Set SUBARR_FORCED_SEGMENT_ENABLED=1 to enable.
    forced_segment_enabled: bool
    # #157 gap-fill: verbose logging knob. When on, the root logger goes to
    # DEBUG and the httpx/httpcore request loggers are UN-pinned from WARNING so
    # request detail shows. Default off = today's INFO behaviour byte-for-byte.
    debug: bool
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
    # #357: chunk-probability threshold T for the confident-multilingual rule.
    # A language counts as high-confidence when >=1 detection chunk reports
    # probability >= T; >=2 such languages => the file is multilingual (The
    # Beasts). Default 0.5 shipped as an env-overridable placeholder — empirical
    # tuning is deferred until per-chunk probabilities have accrued from normal
    # detection. Override with SUBARR_MULTILANG_CHUNK_MIN_PROB.
    multilang_chunk_min_prob: float

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

    # #198: optional API key (arr convention). When set, every /api/* call
    # (except health, the same-origin UI bootstrap, and static) requires
    # X-Api-Key or ?apikey=. Empty = no key (the bundled UI on a trusted LAN
    # just works). Orthogonal to basic auth — basic auth gates the human/
    # browser surface, the key gates programmatic + cross-origin access.
    api_key: str
    # #198: same-origin CSRF gate on unsafe /api/* methods. On by default
    # (the API can restart subgen / mutate Sonarr; a malicious page POSTing
    # at a LAN IP must not reach it). Set SUBARR_CSRF_PROTECTION=0 only if a
    # trusted non-browser client trips it.
    csrf_protection: bool

    # #238 forced auth.
    # SUBARR_AUTH_DISABLED — turn built-in auth fully off (a reverse proxy owns
    # auth: Authelia / Caddy / Traefik). Skips the setup gate + the no-auth banner.
    auth_disabled: bool
    # SUBARR_AUTH_RESET — clear the stored credential on boot, back to first-run
    # setup. A recovery lever; document alongside the env override + CLI.
    auth_reset: bool
    # SUBARR_COOKIE_SAMESITE — session cookie SameSite: lax (default) / strict /
    # none. `none` is for embedding subarr in a cross-site dashboard iframe and
    # forces Secure (https), per browser rules.
    cookie_samesite: str
    # SUBARR_SESSION_SECRET — signs the session cookie. Set it (any long random
    # string) to keep logins across restarts; empty ⇒ an ephemeral per-boot
    # secret (sessions reset on restart — you just log in again, never locked
    # out). Empty is the off-signal, so bare get (not _env_or).
    session_secret: str

    # #260 login brute-force throttle. trusted_proxies: CIDRs whose
    # X-Forwarded-For we believe (to key the throttle on the real client IP
    # behind a reverse proxy). login_allowlist: CIDRs exempt from the throttle
    # entirely ("never block my LAN"). Both empty by default (no XFF trust, no
    # exemptions). max_attempts/window_s tune the sliding window.
    trusted_proxies: str
    login_allowlist: str
    login_max_attempts: int
    login_window_s: int

    # Filesystem prefix subgen prepends to canonical paths inside its container.
    # /api/coverage uses this to map a Sonarr/Radarr `path` field back to the
    # canonical-to-subarr form used everywhere else (relative to media_root).
    # Subgen sees Sonarr/Radarr paths as /data/Media/<...>; Subarr sees the
    # same files at /media/library/<...>. This prefix is what Sonarr/Radarr
    # store as `path`; we strip it to canonicalise.
    # #134 Phase 1: per-library arr_prefix (in Library) supersedes the old
    # #133 sonarr_path_prefix / radarr_path_prefix split, which was defined
    # but consumed nowhere — removed in favour of libraries[].
    arr_path_prefix: str

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

    # #161 Phase 1: validated instance list (flat tuple; each Instance carries
    # its .service). Per-service instance 0 (id "") mirrors the legacy
    # sonarr_url/api_key/... scalars for back-compat; extras come from the
    # override store's "instances" key. Built in load() after scalars+overrides.
    instances: tuple[Instance, ...] = ()


def load() -> Settings:
    # See _env_or docstring for the empty-string fall-through rule (#127).
    # Helper applied where empty is semantically meaningless. Bare .get() kept
    # for fields where empty IS the intended off-signal (API keys, telemetry,
    # auth, optional docker discovery).
    _s = Settings(
        media_root=Path(_env_or("SUBARR_MEDIA_ROOT", "/media/library")),
        subgen_compose_path=Path(_env_or("SUBGEN_COMPOSE_PATH", "/dockercontainers/subgen/compose.yaml")),
        subgen_url=_env_or("SUBGEN_URL", DEFAULT_SUBGEN_URL),
        subgen_container=_env_or("SUBGEN_CONTAINER", "subgen"),
        subgen_media_prefix=_env_or("SUBGEN_MEDIA_PREFIX", "/media"),
        db_path=Path(_env_or("SUBARR_DB_PATH", "/data/subarr.db")),
        port=int(_env_or("SUBARR_PORT", "9922")),
        plex_url=_env_or("PLEX_URL", "http://192.168.1.105:32400"),
        plex_token=os.environ.get("PLEX_TOKEN", ""),
        plex_section=_env_or("PLEX_SECTION", "all"),
        plex_path_prefix=os.environ.get("PLEX_PATH_PREFIX", ""),
        jellyfin_url=os.environ.get("JELLYFIN_URL", ""),
        jellyfin_api_key=os.environ.get("JELLYFIN_API_KEY", ""),
        jellyfin_path_prefix=os.environ.get("JELLYFIN_PATH_PREFIX", ""),
        plex_partial_scan_enabled=_env_or("PLEX_PARTIAL_SCAN_ENABLED", "1").strip().lower()
        not in ("0", "false", "no", "off"),
        subgen_webhook_enabled=_env_or("SUBARR_SUBGEN_WEBHOOK_ENABLED", "1").strip().lower()
        not in ("0", "false", "no", "off"),
        sonarr_propagate_audio_lang=os.environ.get("SONARR_PROPAGATE_AUDIO_LANG", "0").strip().lower()
        in ("1", "true", "yes", "on"),
        # #359 bake: on by default after the corpus sweep proved it (target_cps=17,
        # min_cue_ms=1000) cuts critical-CPS cues 22.9%->5.2% with zero new overlaps.
        # Opt out with SUBARR_RETIME_ENABLED=0.
        retime_enabled=os.environ.get("SUBARR_RETIME_ENABLED", "1").strip().lower()
        in ("1", "true", "yes", "on"),
        # #364: default OFF (opt-in GPU-spending pipeline).
        forced_segment_enabled=os.environ.get("SUBARR_FORCED_SEGMENT_ENABLED", "0").strip().lower()
        in ("1", "true", "yes", "on"),
        # #157 gap-fill: SUBARR_DEBUG verbose knob. Off by default; the logging
        # setup (app.py) reads settings.debug to raise the root level to DEBUG.
        debug=os.environ.get("SUBARR_DEBUG", "0").strip().lower() in ("1", "true", "yes", "on"),
        plex_audio_hints=os.environ.get("PLEX_AUDIO_HINTS", "0").strip().lower()
        in ("1", "true", "yes", "on"),
        vad_enabled=_env_or("SUBARR_VAD_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off"),
        # #104: default 120s. Min-clamped at 0 (a 0/negative value disables
        # debounce — every kick rebuilds, the pre-#104 behaviour).
        coverage_refresh_min_interval_s=max(
            0.0, float(_env_or("SUBARR_COVERAGE_REFRESH_MIN_INTERVAL_S", "120"))
        ),
        # #357: T default 0.5. Clamped to [0.0, 1.0] since it is a probability.
        multilang_chunk_min_prob=min(1.0, max(0.0, float(_env_or("SUBARR_MULTILANG_CHUNK_MIN_PROB", "0.5")))),
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
        # #198: empty = off (key auth opt-in). The UI reads it via the
        # same-origin /api/ui-bootstrap handout, so a configured key doesn't
        # break the bundled frontend.
        api_key=os.environ.get("SUBARR_API_KEY", ""),
        csrf_protection=_env_or("SUBARR_CSRF_PROTECTION", "1").strip().lower()
        not in ("0", "false", "no", "off"),
        auth_disabled=_env_or("SUBARR_AUTH_DISABLED", "0").strip().lower() in ("1", "true", "yes", "on"),
        auth_reset=_env_or("SUBARR_AUTH_RESET", "0").strip().lower() in ("1", "true", "yes", "on"),
        cookie_samesite=_normalize_samesite(_env_or("SUBARR_COOKIE_SAMESITE", "lax")),
        session_secret=os.environ.get("SUBARR_SESSION_SECRET", ""),
        trusted_proxies=os.environ.get("SUBARR_TRUSTED_PROXIES", ""),
        login_allowlist=os.environ.get("SUBARR_LOGIN_ALLOWLIST", ""),
        login_max_attempts=int(_env_or("SUBARR_LOGIN_MAX_ATTEMPTS", "5")),
        login_window_s=int(_env_or("SUBARR_LOGIN_WINDOW_S", "300")),
        # #136: default 30 days. 0/negative disables arena-run pruning.
        arena_retention_days=int(_env_or("SUBARR_ARENA_RETENTION_DAYS", "30")),
    )
    _apply_persisted_overrides(_s)
    rebuild_libraries(_s)
    rebuild_instances(_s)
    return _s


# Scalars that define the default library (libraries[0]). A runtime edit of any
# of these must trigger rebuild_libraries() — see #285.
LIBRARY_DEFINING_FIELDS = ("media_root", "subgen_media_prefix", "arr_path_prefix")


def rebuild_libraries(s: Settings) -> None:
    """(Re)build ``s.libraries`` from the current scalar config + persisted extras.

    Library 0 = the legacy scalars (``media_root`` / ``subgen_media_prefix`` /
    ``arr_path_prefix``, which a UI/onboarding edit may have changed); extras
    come from the override store's ``libraries`` key. Fail-soft: any config
    error logs and degrades to the single default library so this never breaks.

    Called at load AND whenever a LIBRARY_DEFINING_FIELDS scalar changes at
    runtime (#285) — without the runtime rebuild, editing arr_path_prefix /
    media_root leaves ``libraries[0]`` stale until restart, silently breaking
    all library-aware path resolution.
    """
    from . import config_store

    default_lib = Library(
        slug="",
        name="default",
        fs_root=s.media_root,
        subgen_prefix=s.subgen_media_prefix,
        arr_prefix=s.arr_path_prefix,
    )
    try:
        raw_extras = config_store.load_overrides().get("libraries", [])
        if not isinstance(raw_extras, list):
            raw_extras = []
        libs = build_libraries(default_lib, raw_extras)
    except LibraryConfigError:
        log.warning("invalid libraries[] config; using single default library", exc_info=True)
        libs = (default_lib,)
    object.__setattr__(s, "libraries", libs)
    # #365: at load, instances build right after this (skip the premature check);
    # a runtime library edit re-validates against the already-built instances.
    if s.instances:
        _warn_dangling_bindings(s)


# Scalars that define each service's instance 0. A runtime edit of any of these
# must trigger rebuild_instances() so credential changes take effect live (#161,
# mirrors LIBRARY_DEFINING_FIELDS / #285).
INSTANCE_DEFINING_FIELDS = (
    "sonarr_url",
    "sonarr_api_key",
    "radarr_url",
    "radarr_api_key",
    "bazarr_url",
    "bazarr_api_key",
)


def rebuild_instances(s: Settings) -> None:
    """(Re)build ``s.instances`` from the current scalar config + persisted
    extras. Instance 0 per service = the legacy scalars; extras come from the
    override store's ``instances`` key. Fail-soft: any config error logs and
    degrades to the per-service defaults so this never breaks boot."""
    from . import config_store

    defaults = [
        Instance(id="", service="sonarr", name="default", url=s.sonarr_url, api_key=s.sonarr_api_key),
        Instance(id="", service="radarr", name="default", url=s.radarr_url, api_key=s.radarr_api_key),
        Instance(id="", service="bazarr", name="default", url=s.bazarr_url, api_key=s.bazarr_api_key),
    ]
    try:
        raw_extras = config_store.load_overrides().get("instances", {})
        if not isinstance(raw_extras, dict):
            raw_extras = {}
        insts = build_instances(defaults, raw_extras)
    except InstanceConfigError:
        log.warning("invalid instances config; using per-service defaults", exc_info=True)
        insts = tuple(defaults)
    object.__setattr__(s, "instances", insts)
    # #365: surface library bindings that reference an unconfigured instance.
    _warn_dangling_bindings(s)


def validate_library_bindings(libraries, instances) -> list[str]:
    """#365: return a warning message per library that binds an arr/bazarr
    instance id which isn't configured. Coverage routing degrades a dangling
    binding to instance 0, so these are visibility signals — we warn, never
    raise (a typo must not nuke a whole multi-library config)."""
    by_service: dict[str, set[str]] = {"sonarr": set(), "radarr": set(), "bazarr": set()}
    for inst in instances:
        if inst.service in by_service:
            by_service[inst.service].add(inst.id)
    out: list[str] = []
    for lib in libraries:
        for service, bound in (
            ("sonarr", lib.sonarr_id),
            ("radarr", lib.radarr_id),
            ("bazarr", lib.bazarr_id),
        ):
            if bound and bound not in by_service[service]:
                out.append(
                    f"library {lib.slug or '(default)'!r} binds {service}_id={bound!r} which is not a "
                    f"configured {service} instance; its rows fall back to instance 0"
                )
    return out


def _warn_dangling_bindings(s: Settings) -> None:
    # Runs inside the fail-soft rebuild paths — must never break boot.
    try:
        for msg in validate_library_bindings(s.libraries, s.instances):
            log.warning("%s", msg)
    except Exception:  # noqa: BLE001 — binding validation is advisory, never fatal
        log.warning("library binding validation failed", exc_info=True)


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
    # #71: Jellyfin creds are UI-editable from the outset, same as Plex.
    "jellyfin_url": "JELLYFIN_URL",
    "jellyfin_api_key": "JELLYFIN_API_KEY",
    "jellyfin_path_prefix": "JELLYFIN_PATH_PREFIX",
    # #111/#112: UI-settable toggles. Listed here so env_is_set() lets an
    # explicit env var override a persisted UI choice (env > file > default).
    "vad_enabled": "SUBARR_VAD_ENABLED",
    "plex_audio_hints": "PLEX_AUDIO_HINTS",
    "sonarr_propagate_audio_lang": "SONARR_PROPAGATE_AUDIO_LANG",
    "retime_enabled": "SUBARR_RETIME_ENABLED",
    "forced_segment_enabled": "SUBARR_FORCED_SEGMENT_ENABLED",
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
    "forced_segment_enabled": _coerce_bool,
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
    # #71: Jellyfin creds, same UI-editable/persisted treatment as Plex.
    "jellyfin_url": str,
    "jellyfin_api_key": str,
    "jellyfin_path_prefix": str,
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
