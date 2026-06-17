"""#235: a lightweight per-integration circuit breaker.

task_health (#157) tells us *whether* an upstream is broken; this stops us
*hammering* it. When an integration (Bazarr/Sonarr/Radarr/Plex/Tautulli/Ollama)
fails N times in a row, the breaker OPENs and short-circuits further calls for
a cooldown — cutting wasted requests, slow timeouts, and log spam — then lets a
single probe through (HALF_OPEN) to detect recovery.

In-memory by design: a process restart starts CLOSED (a natural "try again"),
and task_health persists the visibility. Persisting breaker state across
restarts (as sublarr does) is a possible follow-up, not needed for the core
"stop pounding a dead upstream" win.

Trip policy is set by the caller: transport errors + 5xx count as failures
(upstream down/erroring); a <500 HTTP response means the upstream is reachable
(record_success), even a 4xx — hammering an auth/404 won't help but it isn't a
flap, so it must not trip the breaker.
"""

from __future__ import annotations

import time
from collections.abc import Callable

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        *,
        fail_threshold: int = 5,
        cooldown_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._fail_threshold = max(1, fail_threshold)
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._state = CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> str:
        return self._state

    def allow(self) -> bool:
        """True if a call may proceed now. When OPEN, the first call after the
        cooldown transitions to HALF_OPEN and is allowed through as the probe;
        further calls stay blocked until that probe resolves."""
        if self._state == OPEN:
            if self._opened_at is not None and (self._clock() - self._opened_at) >= self._cooldown_s:
                self._state = HALF_OPEN
                return True
            return False
        # CLOSED or HALF_OPEN (probe in flight) — allow.
        return True

    def record_success(self) -> None:
        """A reachable response. Clears failures; a successful probe closes."""
        self._consecutive_failures = 0
        self._opened_at = None
        self._state = CLOSED

    def record_failure(self) -> None:
        """An upstream failure (transport error / 5xx). Trips OPEN at the
        threshold; a failed HALF_OPEN probe re-opens with a fresh cooldown."""
        if self._state == HALF_OPEN:
            self._open()
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._fail_threshold:
            self._open()

    def _open(self) -> None:
        self._state = OPEN
        self._opened_at = self._clock()
