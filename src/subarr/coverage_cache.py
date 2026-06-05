"""v1.1 ARCH FIX: persistent + background-refreshed coverage snapshot.

Before this lived: every /api/coverage call rebuilt end-to-end (60-90s on
large libs hitting Bazarr+Sonarr+Radarr+Tautulli+Calendar+History +
probe reconciliation). The pending-review endpoint did the same. Pages
felt broken because every request blocked on a full rebuild.

After: one SQLite row holds the latest snapshot. A background task
rebuilds it every REFRESH_INTERVAL_S seconds. /api/coverage returns the
row instantly (sub-second). ?fresh=true blocks on a fresh build for the
caller who really wants it now. POST /api/coverage/refresh triggers a
background rebuild without blocking.

Snapshot table holds one row, replaced on each build. Schema deliberately
single-row keyed by id=1 — no history retention needed (we don't render
"snapshot 5 minutes ago" deltas yet; if we do later, this gets a
timestamp index).
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import VIDEO_EXTS

log = logging.getLogger(__name__)


# Schema (coverage_snapshot, single-row) is owned by
# migrations/008_init_schema_parity.sql. run_migrations() runs at boot
# before this cache is constructed, so the table always exists. This cache
# keeps a load() method (not init_schema) purely to warm its in-memory
# mirror from the persisted snapshot on boot.


# Cap the eager-probe fan-out per build. A fresh library can have
# thousands of unprobed rows; probing all at once would hammer ffprobe.
# We probe up to this many per build — the rest drain over subsequent
# builds (each build re-derives the still-unprobed set). 4-wide at the
# walker means this is a few minutes of ffprobe, comfortably inside the
# 5-min refresh cadence.
_EAGER_PROBE_CAP = 400


def eager_probe_targets(items: list[dict[str, Any]], cap: int = _EAGER_PROBE_CAP) -> list[str]:
    """Canonical paths of rows the probe-gate is hiding because they're
    unprobed — i.e. the 'Analyzing' bucket. These are exactly the files we
    must probe to turn them into real gaps (or confirm they're covered).

    Excludes:
    - "probe_failed" rows: those already tried and errored, so re-probing
      every build would be a tight failing loop. They surface in the
      "Couldn't analyze" bucket for manual attention instead.
    - Rows whose canonical_path is NOT a video file — i.e. a show/series
      folder, which happens when the coverage engine couldn't resolve an
      episode file at build time. Probing a folder always errors ("not a
      file") and would manufacture false "couldn't analyze" rows. (The
      underlying "why is a wanted row pointing at a folder?" is a
      coverage-engine resolution gap, handled separately — eager-probe
      just must not act on un-probeable paths.)
    """
    out: list[str] = []
    seen: set[str] = set()
    for it in items:
        if it.get("verification_state") != "unprobed":
            continue
        # Prefer the resolved episode/movie FILE path over canonical_path
        # (which for episodes is the series FOLDER — un-probeable). The
        # folder-row fix propagates file_canonical_path from Sonarr.
        c = it.get("file_canonical_path") or it.get("canonical_path")
        if not c or c in seen:
            continue
        # Must point at an actual video file, not a directory.
        if Path(c).suffix.lower() not in VIDEO_EXTS:
            continue
        seen.add(c)
        out.append(c)
        if len(out) >= cap:
            break
    return out


@dataclass
class CachedSnapshot:
    generated_at: float
    items: list[dict[str, Any]]
    totals: dict[str, Any]
    sources: dict[str, Any]
    build_duration_s: float
    item_count: int

    def age_s(self) -> float:
        return time.time() - self.generated_at

    def to_response(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "items": self.items,
            "totals": self.totals,
            "sources": self.sources,
            "build_duration_s": self.build_duration_s,
            "cached": True,
            "cache_age_s": round(self.age_s(), 1),
        }


class CoverageCache:
    """Single-row SQLite snapshot + thread-safe in-memory mirror for
    fast reads. Writes happen in the background-refresh task; reads
    happen from any FastAPI worker."""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(db_path), check_same_thread=False, isolation_level=None,
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()
        self._cached: CachedSnapshot | None = None
        # Coordination flags: tells the refresh task whether a manual
        # refresh is in flight, and lets readers know if the snapshot
        # is currently being rebuilt.
        self._refresh_lock = asyncio.Lock()
        self._refreshing = False
        # #104 debounce/coalesce state. `request_refresh` is the single
        # entry point for all event-driven kicks (completion / audio-lang
        # verify / manual refresh / queue change). It collapses a burst of
        # kicks into at most one in-flight build + one queued follow-up,
        # and spaces builds at least `_min_interval_s` apart.
        from . import config
        try:
            self._min_interval_s: float = config.load().coverage_refresh_min_interval_s
        except Exception:
            self._min_interval_s = 120.0
        self._last_refresh_done: float = 0.0  # monotonic clock
        self._pending_again: bool = False     # coalesced follow-up flag
        self._pending_args: tuple | None = None  # args for the follow-up
        self._refresh_task: asyncio.Task | None = None
        self._debounce_handle: asyncio.TimerHandle | None = None

    # ─── #104: debounce / coalesce ──────────────────────────────────
    @property
    def min_interval_s(self) -> float:
        return self._min_interval_s

    def set_min_interval_s(self, v: float) -> None:
        self._min_interval_s = max(0.0, float(v))

    def has_pending_refresh(self) -> bool:
        """True when a coalesced follow-up build is queued or a debounce
        timer is armed — i.e. work is still pending beyond the current
        in-flight build."""
        return self._pending_again or self._debounce_handle is not None

    def request_refresh(self, bundle, probe_store, audio_lang_store,
                        *, use_tautulli: bool = True, probe_walker=None) -> None:
        """Single coalescing entry point for event-driven refresh kicks.

        Behaviour:
        - If a build is in flight: set a flag so exactly ONE follow-up
          build runs after it completes (N kicks → 1 follow-up, not N).
        - Else if we're inside the min-interval debounce window since the
          last build: arm (or keep) a single timer to run one build when
          the window elapses. Repeated kicks during the window collapse
          into that one timer.
        - Else: start a build immediately.

        Non-blocking / fire-and-forget. The expensive build always runs
        under `refresh()`'s asyncio.Lock, so even racing callers can't
        double-build."""
        args = (bundle, probe_store, audio_lang_store, use_tautulli, probe_walker)
        self._pending_args = args

        if self._refreshing:
            # A build is running — mark "run once more when done".
            self._pending_again = True
            return

        if self._debounce_handle is not None:
            # A debounce timer is already armed; latest args win (set
            # above), nothing else to do.
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop (shouldn't happen in app context)

        wait = self._time_until_allowed(loop)
        if wait <= 0:
            self._start_refresh_task(args)
        else:
            self._debounce_handle = loop.call_later(
                wait, self._fire_debounced
            )

    def _time_until_allowed(self, loop) -> float:
        if self._min_interval_s <= 0 or self._last_refresh_done == 0.0:
            return 0.0
        elapsed = loop.time() - self._last_refresh_done
        return max(0.0, self._min_interval_s - elapsed)

    def _fire_debounced(self) -> None:
        self._debounce_handle = None
        if self._refreshing:
            self._pending_again = True
            return
        if self._pending_args is not None:
            self._start_refresh_task(self._pending_args)

    def _start_refresh_task(self, args: tuple) -> None:
        bundle, probe_store, audio_lang_store, use_tautulli, probe_walker = args

        async def _run():
            try:
                await self.refresh(
                    bundle, probe_store, audio_lang_store,
                    use_tautulli=use_tautulli, probe_walker=probe_walker,
                )
            except Exception as e:  # pragma: no cover - logged, non-fatal
                log.warning("coverage refresh (coalesced) failed: %s", e)
            finally:
                # If kicks arrived while we built, run exactly one more.
                if self._pending_again:
                    self._pending_again = False
                    if self._pending_args is not None:
                        self._start_refresh_task(self._pending_args)

        self._refresh_task = asyncio.ensure_future(_run())

    def load(self) -> None:
        """Warm the in-memory mirror from the persisted snapshot so the
        first request after boot returns immediately (no cold start). The
        coverage_snapshot table itself is created by migrations."""
        self._load_from_db()

    def _load_from_db(self) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT generated_at, items_json, totals_json, sources_json, "
                "build_duration_s, item_count FROM coverage_snapshot WHERE id = 1"
            ).fetchone()
        if not row:
            return
        try:
            self._cached = CachedSnapshot(
                generated_at=row[0],
                items=json.loads(row[1]),
                totals=json.loads(row[2]),
                sources=json.loads(row[3]),
                build_duration_s=row[4] or 0.0,
                item_count=row[5] or 0,
            )
        except (ValueError, TypeError) as e:
            log.warning("coverage_snapshot row malformed; ignoring: %s", e)
            self._cached = None

    def get_cached(self) -> CachedSnapshot | None:
        return self._cached

    def is_refreshing(self) -> bool:
        return self._refreshing

    def store(self, *, items: list[dict[str, Any]], totals: dict[str, Any],
              sources: dict[str, Any], build_duration_s: float) -> CachedSnapshot:
        snap = CachedSnapshot(
            generated_at=time.time(),
            items=items, totals=totals, sources=sources,
            build_duration_s=build_duration_s,
            item_count=len(items),
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO coverage_snapshot "
                "(id, generated_at, items_json, totals_json, sources_json, build_duration_s, item_count) "
                "VALUES (1, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "  generated_at=excluded.generated_at, "
                "  items_json=excluded.items_json, "
                "  totals_json=excluded.totals_json, "
                "  sources_json=excluded.sources_json, "
                "  build_duration_s=excluded.build_duration_s, "
                "  item_count=excluded.item_count",
                (
                    snap.generated_at,
                    json.dumps(snap.items),
                    json.dumps(snap.totals),
                    json.dumps(snap.sources),
                    snap.build_duration_s,
                    snap.item_count,
                ),
            )
        self._cached = snap
        return snap

    async def refresh(self, bundle, probe_store, audio_lang_store,
                       use_tautulli: bool = True, probe_walker=None,
                       caps_provider=None) -> CachedSnapshot:
        """Run build_coverage and store the result. Uses an asyncio.Lock
        so a parallel refresh waits for the in-flight one instead of
        duplicating the expensive build. Notifications dispatched after
        store so SSE consumers update.

        When `probe_walker` is supplied (PR-C), the build's unprobed rows
        are eager-probed: their files are queued for a targeted ffprobe so
        they flip to verified on a later build. This is what keeps the gap
        list from being silently empty when the user's probe_roots don't
        cover every wanted file. Fire-and-forget — kicking the walker is
        cheap (it backgrounds the actual probing); the next refresh picks
        up the results.
        """
        async with self._refresh_lock:
            self._refreshing = True
            started = time.time()
            try:
                from .coverage_engine import build_coverage
                # #79: resolve subgen caps live (provider) so a watchdog-
                # detected subgen upgrade / IGNORE_FORCED_SUBTITLES toggle is
                # reflected on the next refresh without restarting this loop.
                subgen_caps = caps_provider() if caps_provider else None
                report = await build_coverage(
                    bundle, use_tautulli=use_tautulli,
                    probe_store=probe_store,
                    audio_lang_store=audio_lang_store,
                    subgen_caps=subgen_caps,
                )
                body = report.to_dict()
                duration = time.time() - started
                snap = self.store(
                    items=body["items"],
                    totals=body["totals"],
                    sources=body["sources"],
                    build_duration_s=duration,
                )
                log.info("coverage cache refreshed in %.1fs (%d items)",
                         duration, snap.item_count)
                if probe_walker is not None:
                    await self._kick_eager_probe(probe_walker, body["items"])
                return snap
            finally:
                self._refreshing = False
                # #104: record completion time for the debounce window.
                try:
                    self._last_refresh_done = asyncio.get_running_loop().time()
                except RuntimeError:  # pragma: no cover
                    pass

    async def _kick_eager_probe(self, probe_walker, items: list[dict[str, Any]]) -> None:
        """Queue a targeted probe of the build's unprobed rows. Never lets a
        probe-walker hiccup break the refresh — eager-probe is best-effort."""
        try:
            targets = eager_probe_targets(items)
            if not targets:
                return
            log.info("eager-probe: queueing %d unprobed wanted files", len(targets))
            await probe_walker.probe_paths(targets)
        except Exception as e:
            log.warning("eager-probe kick failed (non-fatal): %s", e)


# Background scheduler — fires refresh on a fixed interval. Started by
# the app lifespan; cancelled at shutdown. Independent of the existing
# coverage_walk schedule (which is user-configurable + much heavier);
# this one is purely the "keep the page snappy" tick.
DEFAULT_INTERVAL_S = 300  # 5 min


async def background_refresh_loop(
    cache: CoverageCache,
    bundle=None,
    probe_store=None,
    audio_lang_store=None,
    interval_s: int = DEFAULT_INTERVAL_S,
    bundle_provider=None,
    probe_walker=None,
    caps_provider=None,
) -> None:
    """Sleep, refresh, repeat. Exits on cancellation.

    The integration bundle is resolved per-refresh via bundle_provider
    (falling back to a directly-passed bundle) so onboarding live-reload
    can swap clients on app.state without restarting this loop.

    `probe_walker` (PR-C) enables eager-probe of unprobed wanted files on
    every refresh, so the probe-gate's gap list populates regardless of
    the user's probe_roots config.
    """
    get_bundle = bundle_provider or (lambda: bundle)
    # Initial refresh on boot if nothing cached yet — fills the snapshot
    # so the first /api/coverage request after a fresh deploy doesn't
    # block for 90s.
    if cache.get_cached() is None:
        log.info("coverage cache: no snapshot found; warming on boot")
        try:
            await cache.refresh(get_bundle(), probe_store, audio_lang_store,
                                probe_walker=probe_walker,
                                caps_provider=caps_provider)
        except Exception as e:
            log.warning("coverage cache: initial warm failed: %s", e)
    while True:
        await asyncio.sleep(interval_s)
        try:
            await cache.refresh(get_bundle(), probe_store, audio_lang_store,
                                probe_walker=probe_walker,
                                caps_provider=caps_provider)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("coverage cache: background refresh failed: %s", e)
