"""#157 gap-fill: an in-process, bounded, exception-proof logging handler.

Feeds two read surfaces of subarr's OWN logs:
  * GET /api/logs/recent          — a filtered snapshot (Health "Recent errors")
  * GET /api/logs/subarr/events   — a live SSE tail (Logs page, source: subarr)

Design constraints (see the #157 gap-fill spec, "Error handling"):
  * `emit` must NEVER raise into logging — a throwing handler breaks every log
    call in the process. Everything inside emit is wrapped; a bad record is
    dropped, never propagated.
  * Bounded memory: the ring is a `deque(maxlen)`, never persisted. Ephemeral +
    local, so no privacy/transmit cost (issue #157 transmit-boundary principle).
  * Live subscribers get a BOUNDED per-subscriber queue; a slow consumer drops
    its oldest event rather than back-pressuring the handler (which would stall
    logging for the whole app).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

# Default ring capacity (bounded memory; a ring, never persisted).
_DEFAULT_MAXLEN = 1000
# Default per-subscriber SSE queue size. Small: a live tail that falls behind
# should drop, not stall the handler.
_DEFAULT_SUBSCRIBER_MAXSIZE = 500

_LEVEL_ORDER = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


class LogRing(logging.Handler):
    """A logging.Handler holding a bounded deque of structured records plus an
    asyncio fan-out for live SSE subscribers."""

    def __init__(
        self,
        *,
        maxlen: int = _DEFAULT_MAXLEN,
        subscriber_maxsize: int = _DEFAULT_SUBSCRIBER_MAXSIZE,
        level: int = logging.INFO,
    ) -> None:
        super().__init__(level=level)
        self._buf: deque[dict] = deque(maxlen=maxlen)
        self._subscriber_maxsize = subscriber_maxsize
        # Live SSE subscribers. Each is a bounded asyncio.Queue[dict].
        self._subscribers: list[asyncio.Queue] = []

    # ── logging.Handler interface ─────────────────────────────────────────
    def emit(self, record: logging.LogRecord) -> None:
        """Store a structured view of the record + fan it out. NEVER raises."""
        try:
            rec = self._to_dict(record)
        except Exception:
            # A malformed record must not break logging. Drop it.
            return
        try:
            self._buf.append(rec)
        except Exception:
            return
        # Fan out to live subscribers (best-effort; a full queue drops oldest).
        for q in list(self._subscribers):
            self._offer(q, rec)

    def _to_dict(self, record: logging.LogRecord) -> dict:
        # record.getMessage() applies %-args and can raise on a mismatch;
        # that's caught by emit's try/except, so a broken record is dropped.
        exc_text = None
        if record.exc_info:
            try:
                exc_text = logging.Formatter().formatException(record.exc_info)
            except Exception:
                exc_text = None
        return {
            "ts": record.created if getattr(record, "created", None) else time.time(),
            "level": record.levelname,
            "logger_name": record.name,
            "message": record.getMessage(),
            "exc_text": exc_text,
        }

    # ── snapshot (GET /api/logs/recent) ───────────────────────────────────
    def snapshot(self, *, level: str | None = None, limit: int | None = None) -> list[dict]:
        """The ring, oldest-first, optionally filtered to `level` and above, and
        tailed to the newest `limit`. Returns copies so callers can't mutate the
        ring. Never raises."""
        try:
            records = list(self._buf)
        except Exception:
            return []
        if level:
            threshold = _LEVEL_ORDER.get(level.upper())
            if threshold is not None:
                records = [
                    r for r in records if _LEVEL_ORDER.get(r.get("level", "INFO"), logging.INFO) >= threshold
                ]
        if limit is not None:
            # limit=0 means "newest 0" = nothing; records[-0:] would be the whole
            # list, so guard it explicitly. None = no limit (all records).
            records = records[-limit:] if limit > 0 else []
        return [dict(r) for r in records]

    # ── live fan-out (GET /api/logs/subarr/events) ────────────────────────
    def subscribe(self) -> asyncio.Queue:
        """Register a new SSE subscriber; returns its bounded queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=self._subscriber_maxsize)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    def _offer(self, q: asyncio.Queue, rec: dict) -> None:
        """Enqueue without blocking. If the subscriber is full, drop its oldest
        so a slow consumer never back-pressures the handler."""
        try:
            q.put_nowait(rec)
        except asyncio.QueueFull:
            try:
                q.get_nowait()  # drop oldest
                q.put_nowait(rec)
            except Exception:
                pass
        except Exception:
            pass
