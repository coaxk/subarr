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


def test_build_coverage_no_cross_instance_collision(anime_stack_full):
    """The SAME raw series/episode id 11/1011 lives in BOTH Sonarrs under
    different paths (Show TV vs Naruto). A single global pass would collapse
    them into one row; the per-instance assembly (#161 T7) must yield TWO
    distinct, library-tagged rows."""
    from subarr.coverage_engine import build_coverage

    report = asyncio.run(build_coverage(anime_stack_full, use_tautulli=False))
    rows = report.to_dict()["items"]

    # both stacks represented, by library label
    libs = {r["library"]["slug"] for r in rows}
    assert "" in libs and "anime" in libs

    # the shared raw episode id did NOT collapse into a single row
    tv = [r for r in rows if r["title"] == "Show TV"]
    naruto = [r for r in rows if r["title"] == "Naruto"]
    assert len(tv) == 1 and len(naruto) == 1
    assert tv[0]["library"]["slug"] == "" and naruto[0]["library"]["slug"] == "anime"
    # distinct canonical files (different libraries / paths)
    assert tv[0]["file_canonical_path"] != naruto[0]["file_canonical_path"]


def test_dedup_does_not_cross_suppress_instances(anime_stack_full):
    """#161 Task 8: a raw bazarr_episode_id shared across two Sonarr instances
    must not cross-suppress — both rows survive with distinct canonicals."""
    from subarr.coverage_engine import build_coverage

    report = asyncio.run(build_coverage(anime_stack_full, use_tautulli=False))
    canons = [r.get("file_canonical_path") for r in report.to_dict()["items"]]
    nonempty = [c for c in canons if c]
    # the duplicated raw id produced two distinct library-tagged canonicals
    assert len(set(nonempty)) == len(nonempty)
