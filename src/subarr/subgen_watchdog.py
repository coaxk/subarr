"""#229: background subgen identity watchdog.

Re-probes subgen capabilities every interval and watches for restart
signals (patch_rev change, version change, reachable flipping false→true
after being seen reachable). When a restart is detected:

  - Updates app_.state.subgen_caps to the fresh probe result
  - Stamps app_.state.subgen_restart_detected_at with the wall-clock time
  - Logs at WARNING so it's surfaced in operations logs
  - Calls the reconciliation hook (provided by app startup)

The reconciliation hook is decoupled so this module doesn't have to
know about scan_store / completion_watcher / coverage cache. Caller
passes a callable; we call it with (old_caps, new_caps, detected_at).

Restart detection is conservative: we only declare a restart when we
SEE the identity change. Subgen briefly unreachable does NOT count as
a restart — we just keep the old caps until reachability returns. Most
restarts produce a brief unreachable window followed by a new identity,
which trips the detection on the next successful probe.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)


# Re-probe interval. 30s matches the dashboard cache cadence — most
# restarts that happen between user actions are caught within one cycle.
# Faster polls add noise; slower ones leave orphans in flight longer.
DEFAULT_INTERVAL_S = 30

# A single missed probe is a transient blip (DNS hiccup, brief network
# stall), NOT a restart. Require this many consecutive unreachable probes
# before treating an unreachable→reachable transition as a same-version
# bounce. Prevents false "subgen restarted / lost on restart" alarms.
_BOUNCE_MIN_UNREACHABLE = 2


class SubgenWatchdog:
    """Owns the periodic re-probe loop. Lifecycle: start() / stop()."""

    def __init__(
        self,
        subgen=None,
        get_caps: Callable[[], Any] = None,
        set_caps: Callable[[Any], None] = None,
        on_restart: Callable[[Any, Any, float], Awaitable[None]] | None = None,
        interval_s: int = DEFAULT_INTERVAL_S,
        subgen_provider=None,
    ):
        # Resolve subgen live so onboarding live-reload (which swaps and
        # closes the boot client) doesn't leave the watchdog probing a
        # dead client and pinning caps to 'unreachable'.
        self._subgen_provider = subgen_provider or (lambda: subgen)
        self._get_caps = get_caps
        self._set_caps = set_caps
        self._on_restart = on_restart
        self._interval_s = interval_s
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        # Last time we detected a restart. Surfaced via app state for
        # the UI ("Subgen restarted 4m ago — 12 items orphaned").
        self.last_restart_at: float | None = None
        self.last_restart_from: dict[str, Any] | None = None
        self.last_restart_to: dict[str, Any] | None = None
        # Consecutive unreachable probes. Detects same-version container
        # bounces (docker restart with unchanged patch_rev) while ignoring
        # single transient blips — see _BOUNCE_MIN_UNREACHABLE. (A real
        # restart is confirmed evidence-side in mark_orphaned_before, which
        # won't orphan items still present in subgen's live queue.)
        self._consecutive_unreachable = 0

    @property
    def _subgen(self):
        """Current subgen client (resolved live for onboarding reload)."""
        return self._subgen_provider()

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _run(self) -> None:
        # Initial delay so we don't slam subgen the moment app starts —
        # the boot probe just happened. First check is one interval out.
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=self._interval_s)
            return  # stop() called during initial delay
        except asyncio.TimeoutError:
            pass

        while not self._stop.is_set():
            try:
                await self._probe_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("subgen watchdog: probe failed: %s", e)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_s)
                return
            except asyncio.TimeoutError:
                continue

    async def _probe_once(self) -> None:
        old = self._get_caps()
        new = await self._subgen.probe_capabilities()

        # Detect restart. Two signals:
        #   (a) patch_rev or version differs from last reachable probe
        #       (handles version upgrades + image swaps)
        #   (b) identity unchanged but we observed an unreachable window
        #       between the last reachable and now (handles
        #       `docker restart subgen-next` with the same image)
        if not new.reachable:
            log.debug("subgen watchdog: not reachable on probe; keeping old caps")
            self._consecutive_unreachable += 1
            return

        if old is None or not getattr(old, "reachable", False):
            # We didn't have a reachable baseline before — adopt the new
            # one but don't fire restart (this is initial reachability,
            # not a transition from one-running-version to another).
            self._set_caps(new)
            self._consecutive_unreachable = 0
            log.info("subgen watchdog: subgen reached %s; caps adopted",
                     new.subarr_subgen_patch_rev or new.version)
            return

        old_identity = self._identity(old)
        new_identity = self._identity(new)
        identity_changed = old_identity != new_identity
        # Only a SUSTAINED outage (>= threshold consecutive unreachable
        # probes) counts as a bounce. A single transient blip is ignored,
        # so we don't falsely declare a restart + orphan a healthy queue.
        observed_bounce = self._consecutive_unreachable >= _BOUNCE_MIN_UNREACHABLE
        self._consecutive_unreachable = 0

        if not identity_changed and not observed_bounce:
            # Same subgen still running, never lost reachability. Keep
            # the FRESH caps anyway in case capability flags toggled
            # (e.g. operator changed an env var and the new flag
            # advertises now).
            self._set_caps(new)
            return

        # Restart detected.
        self.last_restart_at = time.time()
        self.last_restart_from = old_identity
        self.last_restart_to = new_identity
        self._set_caps(new)
        reason = ("identity change" if identity_changed
                  else "unreachable→reachable bounce")
        log.warning(
            "subgen RESTART detected (%s): %s → %s. In-flight items "
            "submitted before this point may have evaporated from "
            "subgen's queue.",
            reason, old_identity, new_identity,
        )
        if self._on_restart is not None:
            try:
                await self._on_restart(old, new, self.last_restart_at)
            except Exception as e:
                log.error("subgen watchdog: on_restart hook failed: %s", e,
                          exc_info=True)

    @staticmethod
    def _identity(caps) -> dict[str, Any]:
        """The tuple we use to spot a restart. Patch revision is the
        strongest signal (changes per release); falling back to upstream
        version handles vanilla subgen. Reachable presence is implicit -
        we only call this on reachable probes."""
        return {
            "patch_rev": getattr(caps, "subarr_subgen_patch_rev", None),
            "version": getattr(caps, "version", None),
        }
