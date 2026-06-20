"""Event-loop stall detector + culprit capture.

A background asyncio task updates a heartbeat every `beat_s`. A separate daemon
THREAD watches that heartbeat; when it goes stale (the event loop is blocked by
synchronous work — whether on the main thread or by a GIL-holding worker) it
dumps EVERY thread's current stack at WARNING. That's the exact code freezing
the loop — the class of bug that stalls every concurrent request at once and is
invisible to per-request logging.

Negligible overhead: a 0.1s heartbeat coroutine + a thread that sleeps and
compares a float.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import traceback

log = logging.getLogger(__name__)


def _dump_all_stacks(skip_ids: set[int]) -> str:
    out: list[str] = []
    frames = sys._current_frames()
    names = {t.ident: t.name for t in threading.enumerate()}
    for tid, frame in frames.items():
        if tid in skip_ids:
            continue
        out.append(f"--- thread {names.get(tid, '?')} ({tid}) ---")
        out.append("".join(traceback.format_stack(frame)))
    return "\n".join(out)


async def monitor_event_loop_lag(*, beat_s: float = 0.1, threshold_s: float = 0.5) -> None:
    """Heartbeat coroutine + watchdog thread that logs the stalling stack(s)."""
    state = {"beat": time.monotonic()}
    stop = threading.Event()
    watchdog_id: dict[str, int] = {}

    def _watchdog() -> None:
        watchdog_id["id"] = threading.get_ident()
        reported = False
        while not stop.is_set():
            time.sleep(0.1)
            blocked = time.monotonic() - state["beat"]
            if blocked >= threshold_s:
                if not reported:  # one dump per stall episode, not per tick
                    stacks = _dump_all_stacks(skip_ids={watchdog_id.get("id", 0)})
                    log.warning("event loop STALLED >%.1fs — thread stacks:\n%s", threshold_s, stacks)
                    reported = True
            else:
                reported = False

    watcher = threading.Thread(target=_watchdog, name="loop-lag-watchdog", daemon=True)
    watcher.start()
    try:
        while True:
            state["beat"] = time.monotonic()
            await asyncio.sleep(beat_s)
    except asyncio.CancelledError:
        raise
    finally:
        stop.set()
