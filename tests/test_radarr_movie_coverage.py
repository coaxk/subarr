"""Radarr movie-coverage pass: movies missing English subs surface in Coverage.

Before this, movies reached Coverage ONLY via Bazarr's monitored-wanted list,
so an unmonitored-in-Bazarr movie missing a sub was invisible (a Radarr user
saw zero movies). This pass surfaces every Radarr movie with a file that lacks
English coverage, running the SAME funnel as the wanted path.
"""

from __future__ import annotations

import asyncio
import pytest


def _movie(rid, title):
    return {
        "id": rid,
        "title": title,
        "hasFile": True,
        "monitored": True,
        "path": f"/data/Media/Movies/{title}",
        "movieFile": {"path": f"/data/Media/Movies/{title}/{title}.mkv"},
        "originalLanguage": {"name": "English"},
        "tags": [],
    }


def _probe(canonical, *, embedded_en: bool):
    from subarr.media_probe import AudioStream, ProbeResult, SubtitleStream

    subs = []
    if embedded_en:
        subs = [
            SubtitleStream(
                index=0,
                language="eng",
                codec="subrip",
                title=None,
                default=False,
                forced=False,
                sdh=False,
                commentary=False,
            )
        ]
    return ProbeResult(
        canonical_path=canonical,
        audio=[AudioStream(index=0, language="eng", codec="aac", title=None, default=True)],
        subtitles=subs,
    )


def _run(movies, items, idx):
    from subarr.coverage_engine import _add_radarr_blind_movie_rows

    activity = {"now_playing_titles": set(), "transcoding_titles": set(), "audio_lang_hints": {}}
    return asyncio.run(
        _add_radarr_blind_movie_rows(
            movies,
            items,
            radarr_tags={},
            radarr_missing_ids=set(),
            radarr_recent_ids={},
            activity=activity,
            tt_signals={},
            probe_by_series_prefix=idx,
            failed_idx={},
            user_verifications={},
            whisper_verifications={},
            plex_hints={},
            sources={},
            ignore_forced_subtitles=False,
            ignore_image_subtitles=False,  # [#458]
        )
    )


def test_missing_surfaced_covered_dropped_unprobed_kept(subarr_env):
    movies = [_movie(1, "GapMovie"), _movie(2, "CoveredMovie"), _movie(3, "UnprobedMovie")]
    idx = {
        "Movies/GapMovie": [
            ("Movies/GapMovie/GapMovie.mkv", _probe("Movies/GapMovie/GapMovie.mkv", embedded_en=False))
        ],
        "Movies/CoveredMovie": [
            (
                "Movies/CoveredMovie/CoveredMovie.mkv",
                _probe("Movies/CoveredMovie/CoveredMovie.mkv", embedded_en=True),
            )
        ],
    }
    out = _run(movies, [], idx)
    by_title = {it.title: it for it in out}

    # missing English (probed, no embedded sub) → surfaced as a verified gap
    assert "GapMovie" in by_title
    assert by_title["GapMovie"].verification_state == "verified"
    assert by_title["GapMovie"].media_type == "movie"
    # the FILE path is resolved (so eager-probe / queueing target the .mkv)
    assert by_title["GapMovie"].file_canonical_path == "Movies/GapMovie/GapMovie.mkv"

    # embedded English sub → not a gap → dropped
    assert "CoveredMovie" not in by_title

    # unprobed → kept in the Analyzing bucket (probe-gate resolves it next build)
    assert "UnprobedMovie" in by_title
    assert by_title["UnprobedMovie"].verification_state == "unprobed"
    assert by_title["UnprobedMovie"].file_canonical_path == "Movies/UnprobedMovie/UnprobedMovie.mkv"


def test_dedup_against_existing_movie_rows(subarr_env):
    from subarr.coverage_engine import CoverageItem

    # A movie already rendered from Bazarr-wanted must not be duplicated.
    existing = CoverageItem(
        media_type="movie",
        title="GapMovie",
        canonical_path="Movies/GapMovie",
        file_canonical_path="Movies/GapMovie/GapMovie.mkv",
        bazarr_radarr_id=1,
    )
    # #161 Phase 2: the pass now returns ONLY its new rows (the per-instance
    # accumulator merges them), so a movie already present in existing_items is
    # suppressed and simply absent from the result — not re-emitted.
    out = _run([_movie(1, "GapMovie")], [existing], {})
    assert sum(1 for it in out if it.bazarr_radarr_id == 1) == 0


def test_skips_movies_without_a_file(subarr_env):
    m = _movie(9, "NoFile")
    m["hasFile"] = False
    m["movieFile"] = {}
    out = _run([m], [], {})
    assert out == []


@pytest.mark.asyncio
async def test_all_movie_files_stamps_movieid_from_fanout(subarr_env):
    """#290: the /moviefile fan-out must stamp the movieId it fetched each chunk
    for. Radarr file rows can omit movieId; the old setdefault(movieId, row's-own)
    left it None. Now it's zipped back from the fetched id."""
    from subarr.integrations.radarr import RadarrClient

    c = RadarrClient()

    async def _movies():
        return [{"id": 11, "hasFile": True}, {"id": 22, "hasFile": True}]

    async def _files(mid):
        return [{"path": f"/m/{mid}.mkv"}]  # rows WITHOUT movieId

    c.movies = _movies
    c.movie_files_for_movie = _files
    out = await c.all_movie_files_with_mediainfo()
    by_path = {r["path"]: r for r in out}
    assert by_path["/m/11.mkv"]["movieId"] == 11
    assert by_path["/m/22.mkv"]["movieId"] == 22
