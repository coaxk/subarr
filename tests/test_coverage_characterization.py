"""#161 Phase 2 safety net — the FIRST end-to-end build_coverage harness.

build_coverage was previously 'verified live', never unit-tested end-to-end.
This characterises its single-instance output on a fixed seeded dataset; the
multi-instance refactor (Tasks 6-7) MUST keep this byte-identical. A diff = a
single-instance regression. Reused/extended by the multi-instance tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

GOLDEN = Path(__file__).parent / "data" / "coverage_single_instance_golden.json"

# ── Seeded single-instance dataset (paths under ARR_PATH_PREFIX=/data/Media/) ──
_SONARR_SERIES = [
    {
        "id": 11,
        "title": "Flics",
        "originalLanguage": {"id": 2, "name": "French"},
        "monitored": True,
        "path": "/data/Media/TV/Flics",
        "tags": [],
    },
    {
        "id": 22,
        "title": "Severance",
        "originalLanguage": {"id": 1, "name": "English"},
        "monitored": True,
        "path": "/data/Media/TV/Severance",
        "tags": [],
    },
]
_SONARR_EPS = {
    11: [
        {
            "id": 1011,
            "seriesId": 11,
            "seasonNumber": 1,
            "episodeNumber": 1,
            "hasFile": True,
            "episodeFileId": 101,
            "title": "Pilot",
        },
        {
            "id": 1012,
            "seriesId": 11,
            "seasonNumber": 1,
            "episodeNumber": 2,
            "hasFile": True,
            "episodeFileId": 102,
            "title": "Suite",
        },
    ],
    22: [
        {
            "id": 2201,
            "seriesId": 22,
            "seasonNumber": 1,
            "episodeNumber": 1,
            "hasFile": True,
            "episodeFileId": 201,
            "title": "Macrodata",
        },
    ],
}
_SONARR_FILES = {
    11: [
        {"id": 101, "path": "/data/Media/TV/Flics/Season 1/Flics.S01E01.mkv"},
        {"id": 102, "path": "/data/Media/TV/Flics/Season 1/Flics.S01E02.mkv"},
    ],
    22: [{"id": 201, "path": "/data/Media/TV/Severance/Season 1/Severance.S01E01.mkv"}],
}
_RADARR_MOVIES = [
    {
        "id": 301,
        "title": "Dune",
        "originalLanguage": {"name": "English"},
        "monitored": True,
        "path": "/data/Media/Movies/Dune (2021)",
        "tags": [],
        "movieFile": {"path": "/data/Media/Movies/Dune (2021)/Dune.2021.mkv"},
    },
]
# Bazarr wanted: one normal episode gap (Severance) + one movie gap (Dune).
_BZ_EPS = [
    {
        "sonarrSeriesId": 22,
        "sonarrEpisodeId": 2201,
        "seriesTitle": "Severance",
        "episodeTitle": "Macrodata",
        "episode_number": "1x1",
        "missing_subtitles": [{"code2": "en", "name": "English"}],
    }
]
_BZ_MOVS = [{"radarrId": 301, "title": "Dune", "missing_subtitles": [{"code2": "en", "name": "English"}]}]


def _sonarr(req: httpx.Request) -> httpx.Response:
    p = req.url.path
    if p == "/api/v3/system/status":
        return httpx.Response(200, json={"version": "4.0.0"})
    if p == "/api/v3/series":
        return httpx.Response(200, json=_SONARR_SERIES)
    if p == "/api/v3/tag":
        return httpx.Response(200, json=[])
    if p in ("/api/v3/wanted/missing", "/api/v3/history", "/api/v3/customformat"):
        return httpx.Response(200, json={"records": []} if "wanted" in p or "history" in p else [])
    if p == "/api/v3/calendar":
        return httpx.Response(200, json=[])
    if p == "/api/v3/episode":
        return httpx.Response(200, json=_SONARR_EPS.get(int(req.url.params.get("seriesId")), []))
    if p == "/api/v3/episodefile":
        return httpx.Response(200, json=_SONARR_FILES.get(int(req.url.params.get("seriesId")), []))
    return httpx.Response(404)


def _bazarr(req: httpx.Request) -> httpx.Response:
    p = req.url.path
    if p == "/api/system/status":
        return httpx.Response(200, json={"data": {"bazarr_version": "1.5.6"}})
    if p == "/api/badges":
        return httpx.Response(200, json={"episodes": 1, "movies": 1, "providers": 1})
    if p == "/api/episodes/wanted":
        return httpx.Response(200, json={"data": _BZ_EPS, "total": len(_BZ_EPS)})
    if p == "/api/movies/wanted":
        return httpx.Response(200, json={"data": _BZ_MOVS, "total": len(_BZ_MOVS)})
    if p in ("/api/history", "/api/providers"):
        return httpx.Response(200, json={"data": []})
    return httpx.Response(404)


def _radarr(req: httpx.Request) -> httpx.Response:
    p = req.url.path
    if p == "/api/v3/system/status":
        return httpx.Response(200, json={"version": "5.0.0"})
    if p == "/api/v3/movie":
        return httpx.Response(200, json=_RADARR_MOVIES)
    if p in ("/api/v3/tag", "/api/v3/customformat", "/api/v3/calendar"):
        return httpx.Response(200, json=[])
    if p in ("/api/v3/wanted/missing", "/api/v3/history"):
        return httpx.Response(200, json={"records": []})
    return httpx.Response(404)


def _summary(d: dict) -> list[dict]:
    """Curated, deterministic projection of the report — the fields a refactor
    could plausibly break. Volatile/derived top-level fields are ignored."""
    rows = []
    for it in d.get("items", []):
        rows.append(
            {
                "media_type": it.get("media_type"),
                "title": it.get("title"),
                "canonical_path": it.get("canonical_path"),
                "file_canonical_path": it.get("file_canonical_path"),
                "score": it.get("score"),
                "score_reasons": it.get("score_reasons"),
                "has_sub_on_disk": it.get("has_sub_on_disk"),
                "bazarr_blind": it.get("bazarr_blind"),
                "reason": it.get("reason"),
            }
        )
    rows.sort(
        key=lambda r: (
            r["media_type"] or "",
            r["file_canonical_path"] or r["canonical_path"] or "",
            r["title"] or "",
        )
    )
    return rows


@pytest.mark.asyncio
async def test_build_coverage_single_instance_characterization(coverage_bundle):
    from subarr.coverage_engine import build_coverage

    bundle = coverage_bundle(sonarr_handler=_sonarr, bazarr_handler=_bazarr, radarr_handler=_radarr)
    report = await build_coverage(
        bundle, use_tautulli=False, probe_store=None, audio_lang_store=None, subgen_caps=None
    )
    actual = json.dumps(_summary(report.to_dict()), sort_keys=True, indent=2)

    if not GOLDEN.exists():
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(actual, encoding="utf-8")
        pytest.skip("golden recorded — re-run to assert (review the file first)")
    assert actual == GOLDEN.read_text(encoding="utf-8")
