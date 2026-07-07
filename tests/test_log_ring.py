"""#157 gap-fill: LogRing — a bounded, exception-proof in-process logging
handler feeding /api/logs/recent (snapshot) and /api/logs/subarr/events (SSE)."""

from __future__ import annotations

import asyncio
import logging

import pytest

from subarr.log_ring import LogRing


def _record(name="subarr.test", level=logging.INFO, msg="hello", args=()):
    return logging.LogRecord(
        name=name, level=level, pathname=__file__, lineno=1, msg=msg, args=args, exc_info=None
    )


def test_captures_structured_record():
    ring = LogRing(maxlen=10)
    ring.emit(_record(msg="a message"))
    snap = ring.snapshot()
    assert len(snap) == 1
    rec = snap[0]
    assert rec["message"] == "a message"
    assert rec["level"] == "INFO"
    assert rec["logger_name"] == "subarr.test"
    assert "ts" in rec and isinstance(rec["ts"], float)
    assert rec["exc_text"] is None


def test_caps_at_maxlen():
    ring = LogRing(maxlen=3)
    for i in range(5):
        ring.emit(_record(msg=f"m{i}"))
    snap = ring.snapshot()
    assert len(snap) == 3
    assert [r["message"] for r in snap] == ["m2", "m3", "m4"]  # oldest dropped


def test_snapshot_filters_by_level():
    ring = LogRing(maxlen=10)
    ring.emit(_record(level=logging.INFO, msg="info line"))
    ring.emit(_record(level=logging.WARNING, msg="warn line"))
    ring.emit(_record(level=logging.ERROR, msg="err line"))
    warn_plus = ring.snapshot(level="WARNING")
    assert [r["message"] for r in warn_plus] == ["warn line", "err line"]
    assert ring.snapshot(level="ERROR") == [r for r in warn_plus if r["level"] == "ERROR"]


def test_snapshot_honours_limit():
    ring = LogRing(maxlen=10)
    for i in range(6):
        ring.emit(_record(msg=f"m{i}"))
    snap = ring.snapshot(limit=2)
    assert [r["message"] for r in snap] == ["m4", "m5"]  # newest 2, chronological


def test_snapshot_limit_zero_returns_empty():
    # limit=0 means "newest 0" = nothing (guards the records[-0:]==whole-list
    # footgun; both endpoints accept limit/tail=0). None still means all.
    ring = LogRing(maxlen=10)
    for i in range(4):
        ring.emit(_record(msg=f"m{i}"))
    assert ring.snapshot(limit=0) == []
    assert len(ring.snapshot(limit=None)) == 4


def test_emit_never_raises_on_bad_record():
    ring = LogRing(maxlen=10)
    # %-format mismatch: msg has a placeholder but no args -> getMessage() raises.
    bad = _record(msg="oops %s", args=())
    bad.args = ("only",)  # ok
    good_count_before = len(ring.snapshot())
    # Now a genuinely broken record: msg wants an int arg but gets a dict.
    broken = _record(msg="%d", args=({"not": "an int"},))
    ring.emit(broken)  # must NOT raise
    # The record is dropped (or stored best-effort) but emit returned cleanly.
    assert len(ring.snapshot()) >= good_count_before


def test_captures_exc_text_when_present():
    ring = LogRing(maxlen=10)
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        rec = logging.LogRecord(
            name="subarr.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed",
            args=(),
            exc_info=sys.exc_info(),
        )
    ring.emit(rec)
    snap = ring.snapshot()
    assert snap[0]["exc_text"] is not None
    assert "ValueError: boom" in snap[0]["exc_text"]


@pytest.mark.asyncio
async def test_subscriber_receives_new_record():
    ring = LogRing(maxlen=10)
    q = ring.subscribe()
    try:
        ring.emit(_record(msg="live line"))
        rec = await asyncio.wait_for(q.get(), timeout=1.0)
        assert rec["message"] == "live line"
    finally:
        ring.unsubscribe(q)


@pytest.mark.asyncio
async def test_slow_subscriber_drops_oldest_not_backpressure():
    ring = LogRing(maxlen=100, subscriber_maxsize=2)
    q = ring.subscribe()
    try:
        for i in range(5):
            ring.emit(_record(msg=f"m{i}"))  # never blocks the handler
        # Queue capped at 2: it holds the NEWEST 2, oldest silently dropped.
        got = [await asyncio.wait_for(q.get(), timeout=1.0) for _ in range(2)]
        assert [r["message"] for r in got] == ["m3", "m4"]
        assert q.empty()
    finally:
        ring.unsubscribe(q)
