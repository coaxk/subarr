"""#627: synchronous SQLite and filesystem calls made on the event loop.

On the live instance 28 of 34 loop_lag stalls in a week had the MainThread
inside one of these calls: CoverageCache.store after every refresh, the probe
walkers' stat + cache read + upsert per file, and telemetry loading every probe
path just to count them. Each test fails while its call runs on the loop.
"""

from __future__ import annotations

import asyncio

import pytest


def _on_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def _spy(calls: list[bool], real):
    def wrapper(*a, **k):
        calls.append(_on_loop())
        return real(*a, **k)

    return wrapper


# ─── CoverageCache.refresh → store ─────────────────────────────────────────


class _Report:
    def to_dict(self):
        return {"items": [], "totals": {"items": 0}, "sources": {}}


def test_refresh_stores_snapshot_off_the_event_loop(subarr_env, tmp_path, monkeypatch):
    from subarr import coverage_engine
    from subarr.coverage_cache import CoverageCache
    from subarr.migrate import run_migrations

    db = tmp_path / "t.db"
    run_migrations(db)
    cache = CoverageCache(db)

    async def _build(*a, **kw):
        return _Report()

    monkeypatch.setattr(coverage_engine, "build_coverage", _build)
    calls: list[bool] = []
    monkeypatch.setattr(cache, "store", _spy(calls, cache.store))

    snap = asyncio.run(cache.refresh(bundle=None, probe_store=None, audio_lang_store=None))

    assert calls == [False], "CoverageCache.store ran on the event loop"
    assert cache.get_cached() is snap


# ─── ProbeWalker: stat + cache read + upsert ───────────────────────────────


@pytest.fixture
def walker(subarr_env, tmp_path):
    from subarr.migrate import run_migrations
    from subarr.probe_store import ProbeStore
    from subarr.probe_walker import ProbeWalker

    db = tmp_path / "probe.db"
    run_migrations(db)
    store = ProbeStore(db)
    return ProbeWalker(store), store


def _make_file(canonical: str):
    from subarr.config import settings

    p = settings.media_root / canonical
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"data")
    return p


def _stub_probe(monkeypatch, fail: bool = False):
    from subarr import probe_walker as pw
    from subarr.media_probe import ProbeError, ProbeResult

    async def fake_probe(path, timeout_s=30.0):
        if fail:
            raise ProbeError("boom")
        return ProbeResult(canonical_path="")

    monkeypatch.setattr(pw, "probe", fake_probe)


async def _drain(state):
    while state.status == "running":
        await asyncio.sleep(0.01)
    return state


def _spy_store(monkeypatch, store, names):
    calls: dict[str, list[bool]] = {n: [] for n in names}
    for n in names:
        monkeypatch.setattr(store, n, _spy(calls[n], getattr(store, n)))
    return calls


def test_eager_probe_store_calls_run_off_the_event_loop(walker, monkeypatch):
    wk, store = walker
    _make_file("TV/A/a.mkv")
    _stub_probe(monkeypatch)
    calls = _spy_store(monkeypatch, store, ("get", "upsert"))

    state = asyncio.run(_run(wk.probe_paths(["TV/A/a.mkv"])))

    assert state.probed == 1
    assert calls == {"get": [False], "upsert": [False]}


def test_eager_probe_failure_is_recorded_off_the_event_loop(walker, monkeypatch):
    wk, store = walker
    _make_file("TV/A/a.mkv")
    _stub_probe(monkeypatch, fail=True)
    calls = _spy_store(monkeypatch, store, ("record_failure",))

    state = asyncio.run(_run(wk.probe_paths(["TV/A/a.mkv"])))

    assert state.failures_recorded == 1
    assert calls == {"record_failure": [False]}


def test_root_walk_store_calls_run_off_the_event_loop(walker, monkeypatch):
    wk, store = walker
    _make_file("TV/RootWalk/a.mkv")
    _stub_probe(monkeypatch)
    calls = _spy_store(monkeypatch, store, ("get", "upsert"))

    state = asyncio.run(_run(wk.start_walk("TV/RootWalk")))

    assert state.status == "done", state.errors
    assert state.probed == 1
    assert calls == {"get": [False], "upsert": [False]}


def test_walk_stats_the_file_off_the_event_loop(walker, monkeypatch):
    """The media mount is a network share: a stat can block for as long as
    the share takes to answer."""
    from pathlib import Path

    wk, store = walker
    _make_file("TV/A/a.mkv")
    _stub_probe(monkeypatch)
    calls: list[bool] = []
    real_stat = Path.stat

    def stat(self, *a, **k):
        if self.suffix == ".mkv":
            calls.append(_on_loop())
        return real_stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", stat)

    asyncio.run(_run(wk.probe_paths(["TV/A/a.mkv"])))

    assert calls and not any(calls), "Path.stat ran on the event loop"


async def _run(started):
    return await _drain(await started)


# ─── telemetry: probe row count ────────────────────────────────────────────


def test_probe_store_count(tmp_path):
    from subarr.media_probe import ProbeResult
    from subarr.migrate import run_migrations
    from subarr.probe_store import ProbeStore

    db = tmp_path / "p.db"
    run_migrations(db)
    s = ProbeStore(db)
    assert s.count() == 0
    for p in ("TV/A/a.mkv", "TV/B/b.mkv"):
        s.upsert(canonical_path=p, mtime=1.0, size=1, result=ProbeResult(canonical_path=p))
    assert s.count() == 2


def test_stats_provider_counts_without_loading_every_path():
    from types import SimpleNamespace

    from subarr.telemetry import make_default_stats_provider

    class _Store:
        def count(self):
            return 4321

        def all_paths(self):
            raise AssertionError("all_paths() loads every path just to count them")

    state = SimpleNamespace(probe_store=_Store())
    stats = make_default_stats_provider(state)()
    assert stats["library_bucket"] == "1k-10k"


@pytest.mark.asyncio
async def test_send_now_builds_and_records_off_the_event_loop(tmp_path, monkeypatch):
    from subarr.migrate import run_migrations
    from subarr.telemetry import TelemetryCollector

    db = tmp_path / "t.db"
    run_migrations(db)
    built: list[bool] = []

    def provider():
        built.append(_on_loop())
        return {}

    c = TelemetryCollector(db_path=db, subarr_version="v1.0.0", endpoint="", stats_provider=provider)
    recorded: list[bool] = []
    monkeypatch.setattr(c, "_record_attempt", _spy(recorded, c._record_attempt))

    await c.send_now()
    await c.stop()

    assert built == [False], "build_payload ran on the event loop"
    assert recorded == [False], "_record_attempt ran on the event loop"
