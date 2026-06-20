"""Event-loop stall monitor (loop_lag).

This monitor's stack-dump caught the real culprit behind the UI sluggishness:
a synchronous Path.iterdir() in /api/queue's history build freezing the loop
~1.5s every few seconds (dogfood 2026-06-21). Keep it sharp.
"""

from __future__ import annotations

import asyncio
import logging
import time

import pytest

from subarr.loop_lag import _dump_all_stacks, monitor_event_loop_lag


def test_dump_all_stacks_includes_thread_headers():
    out = _dump_all_stacks(skip_ids=set())
    assert "--- thread" in out
    assert ".py" in out  # at least one frame rendered


@pytest.mark.asyncio
async def test_monitor_logs_on_synchronous_stall(caplog):
    """Blocking the loop synchronously past the threshold must produce a WARNING
    from the watchdog thread (which keeps observing while the loop is frozen)."""
    task = asyncio.create_task(monitor_event_loop_lag(beat_s=0.05, threshold_s=0.3))
    await asyncio.sleep(0.2)  # let the heartbeat establish
    with caplog.at_level(logging.WARNING, logger="subarr.loop_lag"):
        time.sleep(0.8)  # block the event loop (NO await) → heartbeat goes stale
        await asyncio.sleep(0.4)  # let the watchdog fire + heartbeat resume
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert any("STALLED" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_monitor_cancels_cleanly():
    task = asyncio.create_task(monitor_event_loop_lag(beat_s=0.05, threshold_s=10))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
