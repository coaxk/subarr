"""Anonymous usage telemetry.

Per the v1.0 product decision (locked in 00-synthesis.md): ON by
default, opt-out one click. ~1KB/day payload. We publish the
aggregate stats publicly at subarr.com/stats so users see exactly
what we see.

What we collect
---------------
  - install_id        : random UUID, generated once at first boot
  - subarr_version    : __version__ constant
  - python_version    : '3.12.x'
  - os_arch           : 'Linux/x86_64' etc.
  - subgen_kind       : 'subarr-subgen' | 'vanilla' | 'unreachable'
  - subgen_version    : upstream version string when reachable
  - integrations      : {bazarr: bool, sonarr: bool, ...} (configured, not values)
  - library_bucket    : '<100' | '100-1k' | '1k-10k' | '>10k'
  - scheduler_enabled : bool
  - scheduler_mode    : 'dashboard' | 'manual_confirm' | 'auto_rules' | None
  - walks_per_day_30d : rolling 30-day average
  - error_counts_30d  : {ExceptionClass: count}
  - docker_tier       : 1 | 2 | 3 (inferred from config presence)

What we NEVER collect
---------------------
  - File paths, titles, library contents
  - IP addresses, user identifiers, hostnames
  - API keys, tokens, passwords
  - Languages configured (one user might be the only person on subarr
    transcribing Manx Gaelic; a single-user combination of features
    is fingerprintable)
  - Anything else not explicitly enumerated above

Cadence
-------
One ping per 24h. POSTs to settings.telemetry_endpoint when set; no-op
when empty (still records payload locally so Settings can show it,
just doesn't transmit). Failures don't crash — last_error gets set,
prior last_payload preserved.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)


PING_INTERVAL_S = 86400  # 24 hours


@dataclass
class TelemetryPayload:
    """The literal JSON we send. Easy to inspect + test."""

    install_id: str
    sent_at: float
    subarr_version: str
    python_version: str
    os_arch: str
    subgen_kind: str  # 'subarr-subgen' | 'vanilla' | 'unreachable'
    subgen_version: str | None
    integrations: dict[str, bool]
    library_bucket: str
    scheduler_enabled: bool
    scheduler_mode: str | None
    walks_per_day_30d: float
    error_counts_30d: dict[str, int] = field(default_factory=dict)
    # #157 P2: sanitized background-loop crash aggregates over the last 24h,
    # keyed "ExcType:module:line". Type + location + count ONLY — never
    # messages, tracebacks, or paths (those stay local in task_health).
    crash_counts_24h: dict[str, int] = field(default_factory=dict)
    # #196/#202 retention signals. install_age_days = days since this
    # install_id was created (tells genuine retention from id-churn).
    # data_persistent = is /data a real mount (None = couldn't tell).
    install_age_days: float = 0.0
    data_persistent: bool | None = None
    docker_tier: int = 1
    # #202 activation funnel: coarse furthest onboarding step reached + whether
    # the wizard was finished. Lets us see WHERE installs drop off.
    onboarding_step: int | None = None
    onboarding_complete: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "install_id": self.install_id,
            "sent_at": self.sent_at,
            "subarr_version": self.subarr_version,
            "python_version": self.python_version,
            "os_arch": self.os_arch,
            "subgen_kind": self.subgen_kind,
            "subgen_version": self.subgen_version,
            "integrations": self.integrations,
            "library_bucket": self.library_bucket,
            "scheduler_enabled": self.scheduler_enabled,
            "scheduler_mode": self.scheduler_mode,
            "walks_per_day_30d": self.walks_per_day_30d,
            "error_counts_30d": self.error_counts_30d,
            "crash_counts_24h": self.crash_counts_24h,
            "install_age_days": self.install_age_days,
            "data_persistent": self.data_persistent,
            "docker_tier": self.docker_tier,
            "onboarding_step": self.onboarding_step,
            "onboarding_complete": self.onboarding_complete,
        }


@dataclass
class TelemetryState:
    install_id: str
    opted_in: bool
    last_ping_at: float | None
    last_payload_json: str | None
    last_error: str | None
    created_at: float
    # #11: timestamp of the last failed send. Lets the Settings panel
    # show "last error 9m ago" + decide whether the system is
    # currently healthy (last_ping_at newer than last_error_at) or
    # currently degraded (last_error_at newer or no successful ping
    # ever). NULL = no known timestamp (legacy row pre-migration 005).
    last_error_at: float | None = None

    @property
    def last_payload(self) -> dict[str, Any] | None:
        if not self.last_payload_json:
            return None
        try:
            return json.loads(self.last_payload_json)
        except json.JSONDecodeError:
            return None


class TelemetryCollector:
    """Background daily pinger + payload assembler.

    Caller wires data sources (callables that return current stats)
    so we stay decoupled from app.state. Plays nice with tests where
    the sources are mock dicts.
    """

    def __init__(
        self,
        db_path: Path,
        *,
        endpoint: str | None = None,
        subarr_version: str = "0.0.0",
        stats_provider=None,  # callable() -> dict (subarr-side stats)
        subgen_caps_provider=None,  # callable() -> caps-or-None
        subgen_caps_refresher=None,  # async callable() — re-probe subgen before a send (#202)
        interval_s: int = PING_INTERVAL_S,
    ):
        self._db_path = db_path
        self._endpoint = endpoint or ""
        self._subarr_version = subarr_version
        self._stats_provider = stats_provider or (lambda: {})
        self._subgen_caps_provider = subgen_caps_provider or (lambda: None)
        self._subgen_caps_refresher = subgen_caps_refresher
        self._interval_s = interval_s
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._client: httpx.AsyncClient | None = None
        self._health = None  # wired by app.py lifespan (#289)
        # Ensure the row exists on init so /api/telemetry/state never
        # returns "not initialised" before the first tick.
        self._ensure_row()

    # ─── Lifecycle ─────────────────────────────────────────────────

    def start(self) -> None:
        # No endpoint ⇒ telemetry is disabled (opt-out, or the test/CI env that
        # sets SUBARR_TELEMETRY_ENDPOINT=""). Don't spawn the pinger at all — its
        # first tick POSTs immediately, which is how the test suite was polluting
        # the prod endpoint. Manual `send_now()` still works + records locally.
        if not self._endpoint:
            log.info("telemetry disabled (no endpoint) — pinger not started")
            return
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="subarr-telemetry")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ─── Public API ────────────────────────────────────────────────

    def state(self) -> TelemetryState:
        conn = sqlite3.connect(str(self._db_path), isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")  # #291: boot-order-independent WAL
        try:
            row = conn.execute(
                "SELECT install_id, opted_in, last_ping_at, "
                "       last_payload_json, last_error, created_at, "
                "       last_error_at "
                "FROM telemetry_state WHERE id = 1"
            ).fetchone()
            assert row is not None, "telemetry_state row missing — migration 003 didn't run"
            return TelemetryState(
                install_id=row[0],
                opted_in=bool(row[1]),
                last_ping_at=row[2],
                last_payload_json=row[3],
                last_error=row[4],
                last_error_at=row[6],
                created_at=row[5],
            )
        finally:
            conn.close()

    def set_opt_in(self, opted_in: bool) -> None:
        conn = sqlite3.connect(str(self._db_path), isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")  # #291: boot-order-independent WAL
        try:
            conn.execute(
                "UPDATE telemetry_state SET opted_in = ? WHERE id = 1",
                (1 if opted_in else 0,),
            )
        finally:
            conn.close()

    def build_payload(self) -> TelemetryPayload:
        """Assemble the current payload without sending. Used by the
        Settings panel preview + tests."""
        st = self.state()
        stats = self._stats_provider() or {}
        caps = self._subgen_caps_provider()

        if caps is None or not caps.reachable:
            subgen_kind = "unreachable"
            subgen_version = None
        elif caps.is_subarr_subgen:
            subgen_kind = "subarr-subgen"
            subgen_version = caps.version
        else:
            subgen_kind = "vanilla"
            subgen_version = caps.version

        return TelemetryPayload(
            install_id=st.install_id,
            sent_at=time.time(),
            subarr_version=self._subarr_version,
            python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            os_arch=f"{platform.system()}/{platform.machine()}",
            subgen_kind=subgen_kind,
            subgen_version=subgen_version,
            integrations=stats.get("integrations") or {},
            library_bucket=stats.get("library_bucket") or "unknown",
            scheduler_enabled=bool(stats.get("scheduler_enabled")),
            scheduler_mode=stats.get("scheduler_mode"),
            walks_per_day_30d=float(stats.get("walks_per_day_30d") or 0.0),
            error_counts_30d=stats.get("error_counts_30d") or {},
            crash_counts_24h=stats.get("crash_counts_24h") or {},
            # install_age_days is telemetry's own data (created_at), computed
            # here rather than via the provider.
            install_age_days=round(max(0.0, (time.time() - st.created_at) / 86400.0), 1),
            data_persistent=stats.get("data_persistent"),
            docker_tier=int(stats.get("docker_tier") or 1),
            onboarding_step=stats.get("onboarding_step"),
            onboarding_complete=bool(stats.get("onboarding_complete")),
        )

    async def send_now(self) -> tuple[bool, str | None]:
        """Force-send the current payload. Returns (sent_ok, error).

        sent_ok=True means we transmitted. False with error=None means
        we didn't transmit because user is opted out OR no endpoint
        configured. False with error set means the transmission attempt
        failed.
        """
        state = self.state()
        if not state.opted_in:
            return False, None
        # #202: refresh subgen reachability right before snapshotting, so
        # subgen_kind reflects current state rather than the boot/default probe.
        # Best-effort — a refresh failure must never block the send.
        if self._subgen_caps_refresher is not None:
            try:
                await self._subgen_caps_refresher()
            except Exception as e:  # noqa: BLE001 — never break a send on a refresh
                log.debug("telemetry subgen refresh failed (best-effort): %s", e)
        payload = self.build_payload()
        payload_json = json.dumps(payload.to_dict(), separators=(",", ":"))

        # Always record what we WOULD send, even if no endpoint —
        # the Settings preview wants something to display.
        self._record_attempt(payload_json, error=None, transmit=False)

        if not self._endpoint:
            return False, None

        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=3.0, read=8.0, write=5.0, pool=3.0),
                headers={"User-Agent": f"subarr-telemetry/{self._subarr_version}"},
            )
        try:
            r = await self._client.post(
                self._endpoint, content=payload_json, headers={"Content-Type": "application/json"}
            )
            if r.status_code >= 400:
                err = f"HTTP {r.status_code}"
                self._record_attempt(payload_json, error=err, transmit=True)
                return False, err
            self._record_attempt(payload_json, error=None, transmit=True)
            return True, None
        except httpx.HTTPError as e:
            err = f"{type(e).__name__}: {e}"
            self._record_attempt(payload_json, error=err, transmit=True)
            return False, err

    # ─── Internal ───────────────────────────────────────────────────

    def _ensure_row(self) -> None:
        conn = sqlite3.connect(str(self._db_path), isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")  # #291: boot-order-independent WAL
        try:
            existing = conn.execute("SELECT 1 FROM telemetry_state WHERE id = 1").fetchone()
            if not existing:
                install_id = uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO telemetry_state (id, install_id, opted_in, created_at) VALUES (1, ?, 1, ?)",
                    (install_id, time.time()),
                )
                log.info("telemetry: generated install_id=%s (opted in by default)", install_id)
        finally:
            conn.close()

    def _record_attempt(self, payload_json: str, error: str | None, transmit: bool) -> None:
        conn = sqlite3.connect(str(self._db_path), isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")  # #291: boot-order-independent WAL
        try:
            # #11: stamp last_error_at when error is set; clear it on
            # successful sends so the panel can show "healthy now" the
            # moment we recover. Without this the old error string sat
            # in the UI forever even after the next tick succeeded.
            now = time.time()
            err_at = now if error else None
            # We update last_payload_json always (so the user can inspect
            # what we'd send) but last_ping_at only when we actually
            # transmitted (so "last sent" reflects reality).
            if transmit:
                conn.execute(
                    "UPDATE telemetry_state "
                    "SET last_payload_json = ?, last_ping_at = ?, "
                    "    last_error = ?, last_error_at = ? "
                    "WHERE id = 1",
                    (payload_json, now, error, err_at),
                )
            else:
                conn.execute(
                    "UPDATE telemetry_state "
                    "SET last_payload_json = ?, "
                    "    last_error = ?, last_error_at = ? "
                    "WHERE id = 1",
                    (payload_json, error, err_at),
                )
        finally:
            conn.close()

    async def _loop(self) -> None:
        # Send once on start (well-under-rate-limit even with 1000s of
        # users; their daily ticks distribute naturally across the day).
        try:
            await self.send_now()
            _h = getattr(self, "_health", None)  # #289: record the boot send too
            if _h:
                _h.record_success("telemetry", expected_interval_s=self._interval_s)
        except Exception as e:
            _h = getattr(self, "_health", None)
            if _h:
                _h.record_failure("telemetry", e, expected_interval_s=self._interval_s)
            log.exception("initial telemetry send failed: %s", e)
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_s)
                return  # stop requested
            except asyncio.TimeoutError:
                pass
            try:
                await self.send_now()
                _h = getattr(self, "_health", None)  # #289 supervision hook
                if _h:
                    _h.record_success("telemetry", expected_interval_s=self._interval_s)
            except Exception as e:
                _h = getattr(self, "_health", None)
                if _h:
                    _h.record_failure("telemetry", e, expected_interval_s=self._interval_s)
                log.exception("telemetry tick failed: %s", e)


# ─── Default stats provider — reads from app.state ─────────────────


def _onboarding_step(app_state) -> int | None:
    """#202: coarse furthest/current onboarding step (0-11). Best-effort."""
    try:
        return int(app_state.onboarding.get().step)
    except Exception:
        return None


def _onboarding_complete(app_state) -> bool:
    """#202: did the install finish the wizard. Best-effort."""
    try:
        return bool(app_state.onboarding.get().is_complete)
    except Exception:
        return False


def _walks_per_day_30d(app_state) -> float:
    """Coverage activity: scans created in the last 30 days / 30. Fixed
    divisor keeps it comparable across installs (a new install reports a
    low rate, which is correct). Best-effort: 0.0 if the store is
    unavailable."""
    try:
        cutoff = time.time() - 30 * 86400
        return round(app_state.scans.count_since(cutoff) / 30.0, 2)
    except Exception:
        return 0.0


def _error_counts_30d(app_state) -> dict[str, int]:
    """Anonymous {exc_class: count} over the last 30 days. Best-effort."""
    try:
        cutoff = time.time() - 30 * 86400
        return app_state.errors.counts_since(cutoff)
    except Exception:
        return {}


def _crash_counts_24h(app_state) -> dict[str, int]:
    """#157 P2: sanitized {ExcType:module:line: count} over the last 24h
    from the supervised-loop crash store. Best-effort."""
    try:
        cutoff = time.time() - 86400
        return app_state.crashes.counts_since(cutoff)
    except Exception:
        return {}


def make_default_stats_provider(app_state) -> Any:
    """Returns a callable suitable for TelemetryCollector(stats_provider=...)
    that pulls live data off app.state. Kept separate so tests can swap
    in a deterministic mock dict instead."""
    from .config import settings

    def _provider() -> dict[str, Any]:
        # Library size bucket — pulled from probe_store row count as a
        # rough proxy. Won't fingerprint anyone since we bucket coarsely.
        try:
            probe_size = len(app_state.probe_store.all_paths())
        except Exception:
            probe_size = 0
        if probe_size < 100:
            bucket = "<100"
        elif probe_size < 1000:
            bucket = "100-1k"
        elif probe_size < 10000:
            bucket = "1k-10k"
        else:
            bucket = ">10k"

        # Scheduler config
        sched_enabled = False
        sched_mode = None
        try:
            sched_cfg = app_state.schedule.get_config()
            if sched_cfg:
                sched_enabled = bool(sched_cfg.get("enabled"))
            rules = app_state.schedule.get_rules()
            if rules:
                sched_mode = rules.get("mode")
        except Exception:
            pass

        # Integrations — configured = api key is non-empty. Every keyed
        # integration defaults to an empty key, so bool() is only True
        # when the user actually wired it up.
        #
        # #119: ollama has NO key — only a URL that defaults to
        # http://ollama:11434 on every install, so bool(settings.ollama_url)
        # reported "configured" 100% of the time (a measurement artifact,
        # not real adoption). Gate instead on the cached reachability from
        # the integrations-health probe (/api/tags), mirroring how
        # subgen_caps.reachable is cached on app.state. If the probe hasn't
        # run yet (None), treat as not-configured rather than trusting the
        # defaulted URL.
        ollama_probe = getattr(app_state, "ollama_probe_result", None)
        ollama_reachable = bool(ollama_probe and getattr(ollama_probe, "reachable", False))
        integrations = {
            "bazarr": bool(settings.bazarr_api_key),
            "sonarr": bool(settings.sonarr_api_key),
            "radarr": bool(settings.radarr_api_key),
            "tautulli": bool(settings.tautulli_api_key),
            "plex": bool(settings.plex_token),
            "ollama": ollama_reachable,
        }

        # Docker tier inferred from config presence
        if settings.docker_socket_path or settings.docker_proxy_url:
            # Tier 3 if any config-dir env var set; Tier 2 otherwise.
            tier3_signals = any(
                os.environ.get(v)
                for v in (
                    "SUBARR_BAZARR_CONFIG_DIR",
                    "SUBARR_SONARR_CONFIG_DIR",
                    "SUBARR_RADARR_CONFIG_DIR",
                    "SUBARR_TAUTULLI_CONFIG_DIR",
                )
            )
            docker_tier = 3 if tier3_signals else 2
        else:
            docker_tier = 1

        return {
            "integrations": integrations,
            "library_bucket": bucket,
            "scheduler_enabled": sched_enabled,
            "scheduler_mode": sched_mode,
            "walks_per_day_30d": _walks_per_day_30d(app_state),
            "error_counts_30d": _error_counts_30d(app_state),
            "crash_counts_24h": _crash_counts_24h(app_state),
            # #202: persistence verdict cached at boot (None = couldn't tell).
            "data_persistent": getattr(app_state, "data_persistent", None),
            "docker_tier": docker_tier,
            # #202 activation funnel.
            "onboarding_step": _onboarding_step(app_state),
            "onboarding_complete": _onboarding_complete(app_state),
        }

    return _provider
