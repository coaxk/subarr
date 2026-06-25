"""#161 Phase 2 — multi-instance coverage merge (KRDucky topology)."""

from __future__ import annotations

import asyncio


def test_fetch_bazarr_all_tags_source_instance(anime_stack_full):
    from subarr.coverage_engine import _fetch_bazarr_all

    sources: dict = {}
    eps, movs = asyncio.run(_fetch_bazarr_all(anime_stack_full, sources))
    # both bazarr instances contributed episode-wanted items, each tagged
    assert {e["_bazarr_instance"] for e in eps} == {"", "anime"}
    # per-instance health recorded
    assert {i["id"] for i in sources["bazarr"]["instances"]} == {"", "anime"}
    assert sources["bazarr"]["ok"] is True


def test_fetch_bazarr_all_single_instance_rollup(coverage_bundle):
    # single-instance: one bazarr ("") -> instances list of one, rollup like today
    import httpx

    def _bz(req):
        p = req.url.path
        if p == "/api/system/status":
            return httpx.Response(200, json={"data": {"bazarr_version": "1.5.6"}})
        if p == "/api/badges":
            return httpx.Response(200, json={"episodes": 0, "movies": 0, "providers": 0})
        if p in ("/api/episodes/wanted", "/api/movies/wanted"):
            return httpx.Response(200, json={"data": [], "total": 0})
        return httpx.Response(200, json={"data": []})

    from subarr.coverage_engine import _fetch_bazarr_all

    bundle = coverage_bundle(bazarr_handler=_bz)
    sources: dict = {}
    eps, movs = asyncio.run(_fetch_bazarr_all(bundle, sources))
    assert [i["id"] for i in sources["bazarr"]["instances"]] == [""]
    assert sources["bazarr"]["ok"] is True
