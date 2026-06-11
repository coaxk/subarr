"""#157 Phase 2 — sanitized fleet crash aggregates.

CrashStore records (exc_type, module:line) per failure — never messages,
tracebacks, or paths — and the daily telemetry ping carries the 24h counts.
"""

from __future__ import annotations

import time


def _raise_in_module():
    # Raised HERE so the traceback's innermost subarr-adjacent frame is this
    # test module; CrashStore must extract a module:line, not a filesystem path.
    raise ValueError("boom with /secret/path/inside.mkv")


def _make_store(tmp_path):
    from subarr.crash_store import CrashStore
    from subarr.migrate import run_migrations

    db = tmp_path / "t.db"
    run_migrations(db)
    return CrashStore(db)


def test_crash_location_is_module_line_not_path(subarr_env):
    from subarr.crash_store import crash_location

    try:
        _raise_in_module()
    except ValueError as e:
        loc = crash_location(e)
    mod, _, line = loc.partition(":")
    assert "/" not in loc and "\\" not in loc, loc  # never a filesystem path
    assert mod  # a module name
    assert line.isdigit() and int(line) > 0


def test_record_and_counts_since(subarr_env, tmp_path):
    store = _make_store(tmp_path)
    try:
        _raise_in_module()
    except ValueError as e:
        store.record(e)
        store.record(e)
    counts = store.counts_since(time.time() - 60)
    assert len(counts) == 1
    key, n = next(iter(counts.items()))
    assert n == 2
    assert key.startswith("ValueError:")
    # Worker key contract: <=64 chars, no XSS chars, no path separators.
    assert len(key) <= 64
    assert not any(c in key for c in "<>\"'`/\\")


def test_counts_since_respects_cutoff(subarr_env, tmp_path):
    store = _make_store(tmp_path)
    try:
        _raise_in_module()
    except ValueError as e:
        store.record(e, when=time.time() - 90000)  # older than 24h
    assert store.counts_since(time.time() - 86400) == {}


def test_record_never_raises(subarr_env, tmp_path):
    from subarr.crash_store import CrashStore

    # Bogus DB path: record must swallow, not raise (best-effort like ErrorStore).
    store = CrashStore(tmp_path / "nope" / "missing.db")
    store.record(ValueError("x"))  # no traceback, bad db — still no raise


def test_prune_removes_old_rows(subarr_env, tmp_path):
    store = _make_store(tmp_path)
    try:
        _raise_in_module()
    except ValueError as e:
        store.record(e, when=time.time() - 40 * 86400)
        store.record(e)
    store.prune(days=30)
    assert sum(store.counts_since(0).values()) == 1


def test_task_health_failure_feeds_crash_store(subarr_env, tmp_path):
    from subarr.crash_store import CrashStore
    from subarr.migrate import run_migrations
    from subarr.task_health import TaskHealthStore

    db = tmp_path / "t.db"
    run_migrations(db)
    crashes = CrashStore(db)
    health = TaskHealthStore(db, crash_recorder=crashes.record)
    try:
        _raise_in_module()
    except ValueError as e:
        health.record_failure("coverage-cache", e, expected_interval_s=60)
    counts = crashes.counts_since(time.time() - 60)
    assert len(counts) == 1 and next(iter(counts)).startswith("ValueError:")


def test_telemetry_payload_includes_crash_counts(subarr_env, tmp_path):
    """The ping payload carries crash_counts_24h from the stats provider."""
    from subarr.telemetry import TelemetryCollector

    store = _make_store(tmp_path)
    try:
        _raise_in_module()
    except ValueError as e:
        store.record(e)

    col = TelemetryCollector(
        db_path=tmp_path / "t.db",
        stats_provider=lambda: {"crash_counts_24h": store.counts_since(time.time() - 86400)},
    )
    d = col.build_payload().to_dict()
    assert "crash_counts_24h" in d
    assert sum(d["crash_counts_24h"].values()) == 1


def test_counts_since_caps_at_64_keys(subarr_env, tmp_path):
    """The worker rejects objects with >64 keys — counts_since must cap,
    keeping the highest counts."""
    import sqlite3

    store = _make_store(tmp_path)
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    now = time.time()
    for i in range(70):
        # i+1 occurrences of E{i} so higher i = higher count
        for _ in range(i + 1):
            conn.execute(
                "INSERT INTO crash_events (exc_type, location, occurred_at) VALUES (?,?,?)",
                (f"E{i}", f"mod:{i}", now),
            )
    conn.commit()
    counts = store.counts_since(now - 60)
    assert len(counts) == 64
    assert "E69:mod:69" in counts  # highest count kept
    assert "E0:mod:0" not in counts  # lowest dropped
