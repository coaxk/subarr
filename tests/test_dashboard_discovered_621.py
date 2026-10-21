"""#621: the Dashboard's "discovered" tile hydrated the WHOLE media_probe table.

`_discovered_sync` called `probe_store.all_entries()` (a JSON decode plus a
dataclass per row) and then `max()` over the list, only to read a count and the
newest row. At ~40k rows that pure-Python work held the GIL long enough to trip
the loop_lag detector on every dashboard refresh, even off the loop in
`to_thread`. The tile now asks SQLite for exactly what it shows.

It also never showed a size: `all_entries()` does not carry the row's `size`,
so the tile's meta always read as if the file were 0 bytes.

The same issue's second finding: `pending_review` made its audio-lang store
reads (sync SQLite) straight from the async endpoint.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


def _store(tmp_path):
    from subarr.migrate import run_migrations
    from subarr.probe_store import ProbeStore

    db = tmp_path / "p.db"
    run_migrations(db)
    return ProbeStore(db)


def _put(store, path: str, *, size: int, probed_at: float, duration_s: float | None = 60.0):
    from subarr.media_probe import ProbeResult

    r = ProbeResult(canonical_path=path)
    r.duration_s = duration_s
    store.upsert(canonical_path=path, mtime=1.0, size=size, result=r)
    with store._lock:
        store._conn.execute(
            "UPDATE media_probe SET probed_at = ? WHERE canonical_path = ?", (probed_at, path)
        )


# ─── ProbeStore.discovered_summary ─────────────────────────────────────────


def test_summary_empty_table(tmp_path):
    s = _store(tmp_path)
    assert s.discovered_summary() == (0, None)


def test_summary_counts_and_returns_newest_row_with_its_size(tmp_path):
    s = _store(tmp_path)
    _put(s, "Movies/A/a.mkv", size=1_000, probed_at=100.0)
    _put(s, "Movies/B/b.mkv", size=5_000_000_000, probed_at=300.0, duration_s=5400.0)
    _put(s, "Movies/C/c.mkv", size=2_000, probed_at=200.0)

    count, newest = s.discovered_summary()

    assert count == 3
    assert newest == ("Movies/B/b.mkv", 5_000_000_000, 5400.0)


def test_summary_newest_row_is_an_index_lookup_not_a_scan(tmp_path):
    # The newest-row query runs under ProbeStore._lock, which the probe walker's
    # upserts also take, so it must not scan and sort the whole table.
    from subarr.probe_store import _NEWEST_PROBE_SQL

    s = _store(tmp_path)
    with s._lock:
        plan = " | ".join(r[-1] for r in s._conn.execute("EXPLAIN QUERY PLAN " + _NEWEST_PROBE_SQL))

    assert "idx_media_probe_probed_at" in plan, plan
    assert "TEMP B-TREE" not in plan, plan


def test_summary_does_not_hydrate_rows(tmp_path, monkeypatch):
    s = _store(tmp_path)
    _put(s, "x.mkv", size=1, probed_at=1.0)

    def boom(*a, **k):
        raise AssertionError("discovered_summary must not hydrate every row")

    monkeypatch.setattr(type(s), "all_entries", boom)
    assert s.discovered_summary()[0] == 1


# ─── the dashboard tile ────────────────────────────────────────────────────


def test_discovered_tile_uses_summary_and_shows_size(tmp_path, monkeypatch):
    from subarr.routers import home

    s = _store(tmp_path)
    _put(s, "Movies/A/a.mkv", size=1_000, probed_at=100.0)
    _put(s, "Movies/B/b.mkv", size=5_000_000_000, probed_at=300.0, duration_s=5400.0)

    def boom(*a, **k):
        raise AssertionError("the dashboard must not call all_entries()")

    monkeypatch.setattr(type(s), "all_entries", boom)

    stages = asyncio.run(home._stages_block(SimpleNamespace(probe_store=s)))
    tile = next(t for t in stages if t["id"] == "discovered")

    assert tile["count"] == 2
    assert tile["top"] == "/Movies/B/b.mkv"
    assert tile["topMeta"] == home._format_size_meta(5_000_000_000, 5400.0)
    assert tile["topMeta"]  # non-empty: the size actually reached the tile


def test_discovered_tile_empty_store(tmp_path):
    from subarr.routers import home

    stages = asyncio.run(home._stages_block(SimpleNamespace(probe_store=_store(tmp_path))))
    tile = next(t for t in stages if t["id"] == "discovered")

    assert (tile["count"], tile["top"], tile["topMeta"]) == (0, "", "")


# ─── pending_review: store reads off the event loop ────────────────────────


class _SnapCache:
    def __init__(self, items):
        self._snap = SimpleNamespace(items=items)

    def request_refresh(self, *a, **k):
        pass

    def get_cached(self):
        return self._snap


_STORE_READS = (
    "get_all_as_lookup",
    "get_all_sources_as_lookup",
    "get_all_multi_as_lookup",
    "get_track_mismatch_dismissed_set",
)


@pytest.mark.parametrize("method", _STORE_READS)
def test_pending_review_store_reads_run_off_the_event_loop(app_with_stub, monkeypatch, method):
    p = "TV/Show/Season 1/ep0001.mkv"
    app_with_stub.app.state.coverage_cache = _SnapCache(
        [{"file_canonical_path": p, "canonical_path": p, "title": "Show", "audio_label_suspect": True}]
    )
    real_store = app_with_stub.app.state.audio_lang
    real = getattr(real_store, method)
    on_loop: list[bool] = []

    def spy(*a, **k):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real(*a, **k)

    class _Proxy:
        # A separate object, not a patch on the real store: the background
        # coverage refresh captured the real store at startup and reads it on
        # its own schedule, which must not count against this endpoint.
        def __getattr__(self, name):
            return spy if name == method else getattr(real_store, name)

    monkeypatch.setattr(app_with_stub.app.state, "audio_lang", _Proxy())

    r = app_with_stub.get("/api/audio-lang/pending-review")

    assert r.status_code == 200
    assert on_loop == [False], f"{method} ran on the event loop"
