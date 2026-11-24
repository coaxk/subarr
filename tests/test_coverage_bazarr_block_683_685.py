"""#683: the API showed Bazarr asking for languages that only subarr assumed on
rows built outside Bazarr's wanted lists, so the "bazarr" block now carries
Bazarr's own list only. #685: rebuilding items from a cached snapshot dropped
every nested field, so backfill/preview lost the arr ids the enqueue needs.
"""

from __future__ import annotations

import copy
import time

import pytest

from subarr.coverage_engine import CoverageItem


def _wanted_ep() -> CoverageItem:
    return CoverageItem(
        media_type="episode",
        title="Show",
        canonical_path="TV/Show",
        file_canonical_path="TV/Show/S01E01.mkv",
        bazarr_sonarr_id=5,
        bazarr_episode_id=7,
        bazarr_wanted=True,
        missing_subtitles=["en", "nl"],
        audio_langs=["ja"],
        verification_state="verified",
        score=500,
    )


def _pass_movie() -> CoverageItem:
    return CoverageItem(
        media_type="movie",
        title="21 Bridges",
        canonical_path="Movies/21 Bridges (2019)",
        file_canonical_path="Movies/21 Bridges (2019)/21 Bridges 1080p (2019).mp4",
        bazarr_radarr_id=11,
        missing_subtitles=["en"],
        sidecar_no_lang=["21 Bridges 1080p (2019).srt"],
        audio_langs=["en"],
        verification_state="verified",
        score=-4900,
    )


def _pass_ep() -> CoverageItem:
    return CoverageItem(
        media_type="episode",
        title="Other",
        canonical_path="TV/Other",
        file_canonical_path="TV/Other/S01E02.mkv",
        bazarr_sonarr_id=8,
        bazarr_episode_id=9,
        missing_subtitles=["en"],
        audio_langs=["de"],
    )


# ── to_dict ──────────────────────────────────────────────────────────


def test_to_dict_bazarr_wanted_row_nests_bazarr_list_and_mirrors_wanted_langs():
    d = _wanted_ep().to_dict()
    assert d["bazarr"] == {
        "sonarr_id": 5,
        "radarr_id": None,
        "episode_id": 7,
        "missing_subtitles": ["en", "nl"],
    }
    assert d["wanted_langs"] == ["en", "nl"]
    assert d["bazarr_wanted"] is True


def test_to_dict_bazarr_wanted_row_with_empty_list_stays_empty():
    it = CoverageItem(
        media_type="movie",
        title="M",
        canonical_path="Movies/M",
        bazarr_radarr_id=3,
        bazarr_wanted=True,
        missing_subtitles=[],
    )
    d = it.to_dict()
    assert d["bazarr"]["missing_subtitles"] == []
    assert d["wanted_langs"] == []
    assert d["bazarr"]["radarr_id"] == 3


def test_to_dict_radarr_pass_movie_row_hides_assumed_bazarr_list():
    d = _pass_movie().to_dict()
    assert d["bazarr"] == {
        "sonarr_id": None,
        "radarr_id": 11,
        "episode_id": None,
        "missing_subtitles": [],
    }
    assert d["wanted_langs"] == ["en"]
    assert d["bazarr_wanted"] is False


def test_to_dict_sonarr_pass_episode_row_hides_assumed_bazarr_list():
    d = _pass_ep().to_dict()
    assert d["bazarr"] == {
        "sonarr_id": 8,
        "radarr_id": None,
        "episode_id": 9,
        "missing_subtitles": [],
    }
    assert d["wanted_langs"] == ["en"]


def test_to_dict_lists_are_copies():
    it = _wanted_ep()
    d = it.to_dict()
    d["wanted_langs"].append("xx")
    d["bazarr"]["missing_subtitles"].append("yy")
    assert it.missing_subtitles == ["en", "nl"]


# ── from_dict ────────────────────────────────────────────────────────


def test_from_dict_restores_a_bazarr_wanted_row():
    assert CoverageItem.from_dict(_wanted_ep().to_dict()) == _wanted_ep()


def test_from_dict_restores_a_radarr_pass_movie_row():
    item = CoverageItem.from_dict(_pass_movie().to_dict())
    assert item == _pass_movie()
    assert item.missing_subtitles == ["en"]
    assert item.bazarr_radarr_id == 11
    assert item.sidecar_no_lang == ["21 Bridges 1080p (2019).srt"]


def test_from_dict_restores_a_sonarr_pass_episode_row():
    assert CoverageItem.from_dict(_pass_ep().to_dict()) == _pass_ep()


def test_from_dict_old_snapshot_without_wanted_langs_uses_bazarr_list():
    d = _pass_movie().to_dict()
    del d["wanted_langs"]
    d["bazarr"]["missing_subtitles"] = ["en"]
    item = CoverageItem.from_dict(d)
    assert item.missing_subtitles == ["en"]
    assert item.bazarr_radarr_id == 11


def test_from_dict_without_bazarr_key_yields_no_ids_and_no_wanted_langs():
    d = _pass_movie().to_dict()
    del d["bazarr"]
    del d["wanted_langs"]
    item = CoverageItem.from_dict(d)
    assert (item.bazarr_sonarr_id, item.bazarr_radarr_id, item.bazarr_episode_id) == (None, None, None)
    assert item.missing_subtitles == []


def test_from_dict_with_null_bazarr_yields_no_ids_and_no_wanted_langs():
    d = _pass_movie().to_dict()
    d["bazarr"] = None
    del d["wanted_langs"]
    item = CoverageItem.from_dict(d)
    assert (item.bazarr_sonarr_id, item.bazarr_radarr_id, item.bazarr_episode_id) == (None, None, None)
    assert item.missing_subtitles == []


def test_from_dict_partial_bazarr_block_restores_only_the_keys_present():
    d = _pass_movie().to_dict()
    d["bazarr"] = {"radarr_id": 4}
    item = CoverageItem.from_dict(d)
    assert item.bazarr_radarr_id == 4
    assert item.bazarr_sonarr_id is None
    assert item.bazarr_episode_id is None
    assert item.missing_subtitles == ["en"]


def test_from_dict_top_level_field_names_win_over_wanted_langs_and_bazarr():
    d = _pass_movie().to_dict()
    d["missing_subtitles"] = ["fr"]
    d["bazarr_radarr_id"] = 99
    item = CoverageItem.from_dict(d)
    assert item.missing_subtitles == ["fr"]
    assert item.bazarr_radarr_id == 99


def test_from_dict_empty_wanted_langs_wins_over_bazarr_list():
    d = _wanted_ep().to_dict()
    d["wanted_langs"] = []
    item = CoverageItem.from_dict(d)
    assert item.missing_subtitles == []


@pytest.mark.parametrize("value", ["en", None])
def test_from_dict_non_list_wanted_langs_falls_back_to_bazarr_list(value):
    d = _wanted_ep().to_dict()
    d["wanted_langs"] = value
    item = CoverageItem.from_dict(d)
    assert item.missing_subtitles == ["en", "nl"]


def test_from_dict_ignores_computed_keys():
    d = _pass_movie().to_dict()
    d.update({"reason": "no-track", "cache_age_s": 1.5, "score_reasons_extra": ["x"]})
    assert CoverageItem.from_dict(d) == _pass_movie()


# ── the language filter ──────────────────────────────────────────────


def _run_wanted_langs_filter(rows: list[dict], wanted: str) -> dict:
    from subarr.routers.coverage import _apply_filters_and_pack

    return _apply_filters_and_pack(
        {
            "items": copy.deepcopy(rows),
            "totals": {"items": len(rows), "episodes": 0, "movies": len(rows)},
            "generated_at": 0.0,
        },
        now=0.0,
        hide_embedded_en=False,
        hide_stale_disk=False,
        hide_english_audio=False,
        hide_pending_download=False,
        only_wanted_langs=wanted,
    )


def test_language_filter_keeps_a_pass_row_by_wanted_langs():
    out = _run_wanted_langs_filter([_pass_movie().to_dict()], "en")
    assert len(out["items"]) == 1


def test_language_filter_drops_a_pass_row_by_wanted_langs():
    out = _run_wanted_langs_filter([_pass_movie().to_dict()], "nl")
    assert out["items"] == []
    assert out["totals"]["suppressed_by_wanted_langs"] == 1


def test_language_filter_falls_back_for_old_rows_without_wanted_langs():
    row = {"media_type": "movie", "verification_state": "verified", "bazarr": {"missing_subtitles": ["en"]}}
    out = _run_wanted_langs_filter([row], "en")
    assert len(out["items"]) == 1


def test_language_filter_treats_empty_wanted_langs_as_nothing_wanted():
    row = {
        "media_type": "movie",
        "verification_state": "verified",
        "wanted_langs": [],
        "bazarr": {"missing_subtitles": ["en"]},
    }
    out = _run_wanted_langs_filter([row], "en")
    assert out["items"] == []


def test_language_filter_survives_a_null_bazarr():
    row = {"media_type": "movie", "verification_state": "verified", "bazarr": None}
    out = _run_wanted_langs_filter([row], "en")
    assert out["items"] == []


# ── routes ───────────────────────────────────────────────────────────


class _Snap:
    def __init__(self, items):
        self.items = items
        self.generated_at = time.time()


class _CC:
    def __init__(self, items):
        self._snap = _Snap(items)

    def get_cached(self):
        return self._snap


def _hold_feeder(app, paused: bool) -> None:
    rules = app.state.schedule.get_rules()
    rules.queue_paused = paused
    app.state.schedule.set_rules(rules)


def test_backfill_jobs_carry_arr_ids_from_to_dict_rows(app_with_stub, monkeypatch):
    import subarr.backfill as backfill_mod

    app = app_with_stub.app
    app.state.coverage_cache = _CC([_wanted_ep().to_dict(), _pass_movie().to_dict()])
    monkeypatch.setattr(backfill_mod, "eligible_backfill_items", lambda items, rules, **kw: list(items))
    _hold_feeder(app, True)
    try:
        r = app_with_stub.post("/api/queue/backfill")
        assert r.status_code == 200, r.text
        assert r.json()["enqueued"] == 2
        jobs = {j.canonical_path: j for j in app.state.pending_queue.list()}
        ep = jobs["TV/Show/S01E01.mkv"]
        movie = jobs["Movies/21 Bridges (2019)/21 Bridges 1080p (2019).mp4"]
        assert (ep.series_id, ep.sonarr_episode_id, ep.radarr_movie_id) == (5, 7, None)
        assert (movie.series_id, movie.sonarr_episode_id, movie.radarr_movie_id) == (None, None, 11)
    finally:
        _hold_feeder(app, False)


def test_schedule_preview_items_carry_arr_ids_from_to_dict_rows(app_with_stub, monkeypatch):
    from subarr.routers import schedule as schedule_router

    app = app_with_stub.app
    app.state.coverage_cache = _CC([_wanted_ep().to_dict(), _pass_movie().to_dict()])
    recorded: dict = {}

    def _fake_evaluate(items, rules):
        recorded["items"] = list(items)
        return []

    monkeypatch.setattr(schedule_router, "evaluate", _fake_evaluate)
    r = app_with_stub.post("/api/schedule/preview")
    assert r.status_code == 200, r.text
    got = {
        it.title: (it.bazarr_sonarr_id, it.bazarr_episode_id, it.bazarr_radarr_id, it.missing_subtitles)
        for it in sorted(recorded["items"], key=lambda i: i.title)
    }
    assert got["21 Bridges"] == (None, None, 11, ["en"])
    assert got["Show"] == (5, 7, None, ["en", "nl"])
