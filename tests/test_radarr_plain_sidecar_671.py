"""#671: a Radarr movie row with a plain <stem>.srt records the sidecar.

A plain sidecar carries no language token, so subarr does not treat it as
English and the movie stays a gap row — but the row keeps the plain
sidecar(s) so the UI need not call it "No subtitle track present".
"""

from __future__ import annotations

import asyncio
import dataclasses

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


def _seed(media_root, title, *names):
    d = media_root / "Movies" / title
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{title}.mkv").write_bytes(b"")
    for name in names:
        (d / name).write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n")


def _verified_idx(title, *, embedded_en=False):
    return {
        f"Movies/{title}": [
            (f"Movies/{title}/{title}.mkv", _probe(f"Movies/{title}/{title}.mkv", embedded_en=embedded_en))
        ]
    }


def test_verified_plain_srt_is_recorded_and_sorts_low(subarr_env, media_root):
    title = "PlainMovie"
    _seed(media_root, title, f"{title}.srt")
    out = _run([_movie(1, title)], [], _verified_idx(title))
    row = {it.title: it for it in out}[title]

    assert row.verification_state == "verified"
    assert row.sidecar_no_lang == [f"{title}.srt"]
    assert "sidecar .srt has no language in its filename" in row.score_reasons
    assert row.score < 0
    assert row.has_sub_on_disk is False
    assert row.suggest_bazarr_rescan is False
    assert row.bazarr_wanted is False
    assert not any(r.startswith("stale:") for r in row.score_reasons)


def test_verified_en_srt_is_dropped(subarr_env, media_root):
    title = "EnMovie"
    _seed(media_root, title, f"{title}.en.srt")
    out = _run([_movie(1, title)], [], _verified_idx(title))
    assert title not in {it.title for it in out}


def test_verified_plain_and_en_srt_is_dropped(subarr_env, media_root):
    title = "BothMovie"
    _seed(media_root, title, f"{title}.srt", f"{title}.en.srt")
    out = _run([_movie(1, title)], [], _verified_idx(title))
    assert title not in {it.title for it in out}


def test_verified_embedded_en_with_plain_srt_is_dropped(subarr_env, media_root):
    title = "EmbeddedMovie"
    _seed(media_root, title, f"{title}.srt")
    out = _run([_movie(1, title)], [], _verified_idx(title, embedded_en=True))
    assert title not in {it.title for it in out}


def test_unprobed_plain_srt_is_kept_and_recorded(subarr_env, media_root):
    title = "UnprobedMovie"
    _seed(media_root, title, f"{title}.srt")
    out = _run([_movie(1, title)], [], {})
    row = {it.title: it for it in out}[title]

    assert row.verification_state == "unprobed"
    assert row.sidecar_no_lang == [f"{title}.srt"]
    assert row.has_sub_on_disk is False
    assert row.suggest_bazarr_rescan is False


def test_verified_without_srt_has_empty_list_and_no_new_reason(subarr_env, media_root):
    title = "NoSrtMovie"
    _seed(media_root, title)
    out = _run([_movie(1, title)], [], _verified_idx(title))
    row = {it.title: it for it in out}[title]

    assert row.sidecar_no_lang == []
    assert "sidecar .srt has no language in its filename" not in row.score_reasons


def test_verified_srt_for_other_stem_is_not_recorded(subarr_env, media_root):
    title = "OtherStemMovie"
    _seed(media_root, title, "Other Movie.srt")
    out = _run([_movie(1, title)], [], _verified_idx(title))
    row = {it.title: it for it in out}[title]

    assert row.sidecar_no_lang == []


def test_verified_token_srts_are_not_plain(subarr_env, media_root):
    title = "TokenMovie"
    _seed(media_root, title, f"{title}.forced.srt", f"{title}.fr.srt")
    out = _run([_movie(1, title)], [], _verified_idx(title))
    row = {it.title: it for it in out}[title]

    assert row.sidecar_no_lang == []


def test_verified_upper_case_extension_is_recorded(subarr_env, media_root):
    title = "UpperMovie"
    _seed(media_root, title, f"{title}.SRT")
    out = _run([_movie(1, title)], [], _verified_idx(title))
    row = {it.title: it for it in out}[title]

    assert row.sidecar_no_lang == [f"{title}.SRT"]


def test_movie_without_folder_path_does_not_crash(subarr_env, media_root):
    title = "NoPathMovie"
    _seed(media_root, title, f"{title}.srt")
    movie = _movie(1, title)
    movie["path"] = None
    out = _run([movie], [], _verified_idx(title))
    row = {it.title: it for it in out}[title]

    assert row.verification_state == "unprobed"
    assert row.sidecar_no_lang == []


def test_to_dict_and_round_trip_keep_sidecar_no_lang(subarr_env, media_root):
    title = "RoundTripMovie"
    _seed(media_root, title, f"{title}.srt")
    out = _run([_movie(1, title)], [], _verified_idx(title))
    row = {it.title: it for it in out}[title]

    from subarr.coverage_engine import CoverageItem

    assert row.to_dict()["sidecar_no_lang"] == [f"{title}.srt"]
    field_names = {f.name for f in dataclasses.fields(CoverageItem)}
    rebuilt = CoverageItem(**{k: v for k, v in row.to_dict().items() if k in field_names})
    assert rebuilt.sidecar_no_lang == [f"{title}.srt"]


@pytest.mark.parametrize(
    ("srt_path", "file_stem", "expected"),
    [
        ("Movie.srt", "Movie", True),
        ("Movie.SRT", "Movie", True),
        ("sub/dir/Movie.srt", "Movie", True),
        ("Movie.en.srt", "Movie", False),
        ("Movie.forced.srt", "Movie", False),
        ("Movie 2.srt", "Movie", False),
        ("movie.srt", "Movie", False),
        ("Movie.ass", "Movie", False),
        ("Movie.srt", "", False),
    ],
)
def test_is_plain_sidecar_for_truth_table(srt_path, file_stem, expected):
    from subarr.coverage_engine import _is_plain_sidecar_for

    assert _is_plain_sidecar_for(srt_path, file_stem) is expected
