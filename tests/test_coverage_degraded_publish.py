"""#167 — a degraded build must not clobber a good warm snapshot.

Root cause: build_coverage treats integration failure as empty data
(best-effort by design), so a transient Sonarr/Bazarr outage right after a
stack restart yields a structurally-valid build where every row is unprobed
(no series paths) and synthetic rows vanish. CoverageCache.refresh published
ANY completed build, replacing the warm snapshot with the degraded one for
~10 minutes until later builds healed it.

Fix: publish gate — hold a build whose critical source failed
(configured + ok:false) while the cached snapshot had that source healthy,
capped at 3 consecutive holds so a genuinely-down integration can't pin a
stale snapshot forever.
"""

from __future__ import annotations

import asyncio


# ─── pure gate decision ──────────────────────────────────────────────


def test_degraded_when_critical_source_newly_failing():
    from subarr.coverage_cache import degraded_sources

    new = {"sonarr": {"ok": False, "configured": True, "error": "boom"}}
    cached = {"sonarr": {"ok": True, "configured": True}}
    assert degraded_sources(new, cached) == ["sonarr"]


def test_not_degraded_when_source_was_already_failing():
    from subarr.coverage_cache import degraded_sources

    new = {"sonarr": {"ok": False, "configured": True, "error": "boom"}}
    cached = {"sonarr": {"ok": False, "configured": True, "error": "boom"}}
    assert degraded_sources(new, cached) == []


def test_not_degraded_when_unconfigured():
    from subarr.coverage_cache import degraded_sources

    # Radarr intentionally unwired: configured False is not a failure.
    new = {"radarr": {"ok": False, "configured": False}}
    cached = {"radarr": {"ok": True, "configured": True}}
    assert degraded_sources(new, cached) == []


def test_not_degraded_without_cached_baseline():
    from subarr.coverage_cache import degraded_sources

    new = {"bazarr": {"ok": False, "configured": True}}
    assert degraded_sources(new, None) == []
    assert degraded_sources(new, {}) == []


# ─── refresh-level behavior ──────────────────────────────────────────


class _FakeReport:
    def __init__(self, sources, items=None):
        self._sources = sources
        self._items = items or []

    def to_dict(self):
        return {"items": self._items, "totals": {"items": len(self._items)}, "sources": self._sources}


def _warm_cache(tmp_path):
    """CoverageCache with a healthy persisted snapshot."""
    from subarr.coverage_cache import CoverageCache
    from subarr.migrate import run_migrations

    db = tmp_path / "t.db"
    run_migrations(db)
    cache = CoverageCache(db)
    cache.store(
        items=[{"canonical_path": "TV/Show", "verification_state": "verified"}],
        totals={"items": 1},
        sources={"sonarr": {"ok": True, "configured": True}, "bazarr": {"ok": True, "configured": True}},
        build_duration_s=1.0,
    )
    return cache


def test_refresh_holds_degraded_build(subarr_env, tmp_path, monkeypatch):
    from subarr import coverage_engine

    cache = _warm_cache(tmp_path)
    good = cache.get_cached()

    async def _degraded_build(*a, **kw):
        return _FakeReport(
            {
                "sonarr": {"ok": False, "configured": True, "error": "down"},
                "bazarr": {"ok": True, "configured": True},
            },
            items=[{"canonical_path": None, "verification_state": "unprobed"}] * 5,
        )

    monkeypatch.setattr(coverage_engine, "build_coverage", _degraded_build)
    snap = asyncio.run(cache.refresh(bundle=None, probe_store=None, audio_lang_store=None))
    # The warm snapshot survives; the degraded build was NOT published.
    assert snap.generated_at == good.generated_at
    assert cache.get_cached().generated_at == good.generated_at
    assert cache.get_cached().items[0]["verification_state"] == "verified"


def test_refresh_publishes_after_hold_cap(subarr_env, tmp_path, monkeypatch):
    """A genuinely-down integration must not pin a stale snapshot forever:
    after 3 consecutive holds the degraded build publishes."""
    from subarr import coverage_engine

    cache = _warm_cache(tmp_path)
    good = cache.get_cached()

    async def _degraded_build(*a, **kw):
        return _FakeReport(
            {"sonarr": {"ok": False, "configured": True, "error": "down"}},
            items=[{"canonical_path": None, "verification_state": "unprobed"}],
        )

    monkeypatch.setattr(coverage_engine, "build_coverage", _degraded_build)
    for _ in range(3):  # holds 1..3
        asyncio.run(cache.refresh(bundle=None, probe_store=None, audio_lang_store=None))
        assert cache.get_cached().generated_at == good.generated_at
    # 4th consecutive degraded build publishes (cap exceeded).
    asyncio.run(cache.refresh(bundle=None, probe_store=None, audio_lang_store=None))
    assert cache.get_cached().generated_at != good.generated_at


def test_refresh_healthy_build_publishes_and_resets_holds(subarr_env, tmp_path, monkeypatch):
    from subarr import coverage_engine

    cache = _warm_cache(tmp_path)
    good = cache.get_cached()

    calls = {"n": 0}

    async def _flaky_build(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeReport({"sonarr": {"ok": False, "configured": True, "error": "down"}})
        return _FakeReport(
            {"sonarr": {"ok": True, "configured": True}},
            items=[{"canonical_path": "TV/Show", "verification_state": "verified"}],
        )

    monkeypatch.setattr(coverage_engine, "build_coverage", _flaky_build)
    asyncio.run(cache.refresh(bundle=None, probe_store=None, audio_lang_store=None))
    assert cache.get_cached().generated_at == good.generated_at  # held
    asyncio.run(cache.refresh(bundle=None, probe_store=None, audio_lang_store=None))
    assert cache.get_cached().generated_at != good.generated_at  # healthy → published


def test_clear_track_mismatch_for_clears_cached_flag(tmp_path):
    """#159 follow-up: after a default-track swap, clearing the cached item's
    default_track_mismatch in place lets the Review row drop immediately, without
    waiting for the throttled (120s) rebuild. Only the named file is touched."""
    from subarr.coverage_cache import CoverageCache
    from subarr.migrate import run_migrations

    db = tmp_path / "t.db"
    run_migrations(db)
    cache = CoverageCache(db)
    cache.store(
        items=[
            {
                "file_canonical_path": "TV/Show/S01E03.mkv",
                "default_track_mismatch": True,
                "mismatch_default_track_lang": "fre",
                "mismatch_native_track_lang": "swe",
                "mismatch_native_audio_ordinal": 2,
            },
            {"file_canonical_path": "TV/Show/S01E05.mkv", "default_track_mismatch": True},
        ],
        totals={"items": 2},
        sources={"sonarr": {"ok": True}, "bazarr": {"ok": True}},
        build_duration_s=1.0,
    )

    assert cache.clear_track_mismatch_for("TV/Show/S01E03.mkv") == 1
    items = {i["file_canonical_path"]: i for i in cache.get_cached().items}
    assert items["TV/Show/S01E03.mkv"]["default_track_mismatch"] is False
    assert "mismatch_native_audio_ordinal" not in items["TV/Show/S01E03.mkv"]
    assert items["TV/Show/S01E05.mkv"]["default_track_mismatch"] is True  # untouched
    # idempotent — nothing left to clear
    assert cache.clear_track_mismatch_for("TV/Show/S01E03.mkv") == 0
