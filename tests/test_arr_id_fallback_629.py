"""#629 follow-up: a completed job that carries no Sonarr/Radarr ids resolves
them from the arrs before the Bazarr write-back.

#631 filled ids at enqueue from the coverage snapshot, but that snapshot is
built from Bazarr's WANTED list, so a file that already has a subtitle (the
usual Requeue / Regenerate target) is never in it. Live check 2026-10-21:
requeue of a covered episode -> ledger #2202 with no ids, Bazarr never told.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest


class FakeSonarr:
    def __init__(self, series=None, files=None, episodes=None, fail=False):
        self._series = series or []
        self._files = files or {}
        self._episodes = episodes or {}
        self._fail = fail
        self.series_calls = 0

    def is_configured(self):
        return True

    async def series(self):
        self.series_calls += 1
        if self._fail:
            raise RuntimeError("sonarr down")
        return self._series

    async def episode_files_for_series(self, series_id):
        return self._files.get(series_id, [])

    async def episodes(self, series_id):
        return self._episodes.get(series_id, [])


class FakeRadarr:
    def __init__(self, movies=None):
        self._movies = movies or []
        self.movie_calls = 0

    def is_configured(self):
        return True

    async def movies(self):
        self.movie_calls += 1
        return self._movies


class Unconfigured:
    def is_configured(self):
        return False


def _sonarr_for_showtv():
    return FakeSonarr(
        series=[
            {"id": 7, "path": "/data/tv/ShowTVExtra"},
            {"id": 2081, "path": "/data/tv/ShowTV"},
        ],
        files={2081: [{"id": 500, "path": "/data/tv/ShowTV/Season 1/ShowTV.S01E01.mkv"}]},
        episodes={
            2081: [
                {"id": 74162, "seasonNumber": 1, "episodeNumber": 2, "episodeFileId": 500},
                {"id": 74161, "seasonNumber": 1, "episodeNumber": 1, "episodeFileId": 500},
                {"id": 74999, "seasonNumber": 1, "episodeNumber": 3, "episodeFileId": 501},
            ]
        },
    )


@pytest.fixture
def tv_env(subarr_env, monkeypatch, tmp_path: Path):
    import importlib

    root = tmp_path / "tv"
    (root / "ShowTV" / "Season 1").mkdir(parents=True)
    monkeypatch.setenv("SUBARR_MEDIA_ROOT", str(root))
    monkeypatch.setenv("ARR_PATH_PREFIX", "/data/tv/")
    from subarr import config, paths

    importlib.reload(config)
    importlib.reload(paths)
    return root


def test_resolves_episode_ids_and_picks_the_first_episode_of_a_multi_episode_file(tv_env):
    from subarr.arr_id_lookup import ArrIdResolver

    r = ArrIdResolver()
    ids = asyncio.run(r.resolve("ShowTV/Season 1/ShowTV.S01E01.mkv", _sonarr_for_showtv(), Unconfigured()))
    assert ids == {"series_id": 2081, "sonarr_episode_id": 74161}


def test_resolves_movie_id_by_file_then_by_folder(tv_env):
    from subarr.arr_id_lookup import ArrIdResolver

    radarr = FakeRadarr(
        movies=[
            {
                "id": 11,
                "path": "/data/tv/Films/Other (2001)",
                "movieFile": {"path": "/data/tv/Films/Other (2001)/o.mkv"},
            },
            {
                "id": 42,
                "path": "/data/tv/Films/Heat (1995)",
                "movieFile": {"path": "/data/tv/Films/Heat (1995)/h.mkv"},
            },
            {"id": 43, "path": "/data/tv/Films/NoFile (2020)"},
        ]
    )
    r = ArrIdResolver()
    assert asyncio.run(r.resolve("Films/Heat (1995)/h.mkv", Unconfigured(), radarr)) == {
        "radarr_movie_id": 42
    }
    assert asyncio.run(r.resolve("Films/NoFile (2020)/n.mkv", Unconfigured(), radarr)) == {
        "radarr_movie_id": 43
    }


def test_unknown_path_or_failing_arr_resolves_to_nothing(tv_env):
    from subarr.arr_id_lookup import ArrIdResolver

    r = ArrIdResolver()
    assert asyncio.run(r.resolve("Nope/x.mkv", _sonarr_for_showtv(), FakeRadarr())) == {}
    assert (
        asyncio.run(r.resolve("ShowTV/Season 1/ShowTV.S01E01.mkv", FakeSonarr(fail=True), Unconfigured()))
        == {}
    )


def test_series_and_movie_lists_are_cached_between_lookups(tv_env):
    from subarr.arr_id_lookup import ArrIdResolver

    sonarr = _sonarr_for_showtv()
    radarr = FakeRadarr()
    r = ArrIdResolver()
    for _ in range(3):
        asyncio.run(r.resolve("Nope/x.mkv", sonarr, radarr))
    assert sonarr.series_calls == 1
    assert radarr.movie_calls == 1


def test_set_arr_ids_fills_only_missing_columns(tmp_path):
    from subarr.migrate import run_migrations
    from subarr.provenance import ProvenanceStore

    db = tmp_path / "p.db"
    run_migrations(db)
    store = ProvenanceStore(db)
    a = store.record(canonical_path="A.mkv", scan_id=None)
    b = store.record(canonical_path="B.mkv", scan_id=None, series_id=1, sonarr_episode_id=2)
    store.set_arr_ids(a, series_id=2081, sonarr_episode_id=74161)
    store.set_arr_ids(b, series_id=9, sonarr_episode_id=9)
    rows = {e.id: e for e in store.pending()}
    assert (rows[a].series_id, rows[a].sonarr_episode_id) == (2081, 74161)
    assert (rows[b].series_id, rows[b].sonarr_episode_id) == (1, 2)


def test_completion_resolves_missing_ids_then_uploads_to_bazarr(tv_env, caplog):
    from subarr.completion_watcher import CompletionWatcher
    from subarr.paths import canonical_to_fs

    canonical = "ShowTV/Season 1/ShowTV.S01E01.mkv"
    fs = Path(canonical_to_fs(canonical))
    fs.write_bytes(b"\x00")
    (fs.parent / (fs.stem + ".en.srt")).write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n")

    uploads = []

    class FakeBazarr:
        def is_configured(self):
            return True

        async def upload_episode_subtitle(self, **kw):
            uploads.append(kw)

    sonarr = _sonarr_for_showtv()
    clients = {"bazarr": FakeBazarr(), "sonarr": sonarr, "radarr": Unconfigured()}
    bundle = SimpleNamespace(client_for=lambda svc, _iid: clients[svc])
    written = []
    prov = SimpleNamespace(
        mark_bazarr_triggered=lambda _id: None,
        set_arr_ids=lambda ledger_id, **ids: written.append((ledger_id, ids)),
    )
    w = CompletionWatcher(provenance=prov, bundle_provider=lambda: bundle)
    entry = SimpleNamespace(
        id=2202,
        canonical_path=canonical,
        sonarr_episode_id=None,
        series_id=None,
        radarr_movie_id=None,
        queued_at=0,
    )

    with caplog.at_level(logging.INFO, logger="subarr.completion_watcher"):
        assert asyncio.run(w._try_upload_to_bazarr(entry)) is True
    assert uploads and uploads[0]["series_id"] == 2081 and uploads[0]["episode_id"] == 74161
    assert written == [(2202, {"series_id": 2081, "sonarr_episode_id": 74161})]
    assert (entry.series_id, entry.sonarr_episode_id) == (2081, 74161)
    assert any("resolved" in r.getMessage() and "#2202" in r.getMessage() for r in caplog.records)


def test_completion_with_ids_already_present_never_asks_the_arrs(tv_env):
    from subarr.completion_watcher import CompletionWatcher

    sonarr = _sonarr_for_showtv()
    clients = {"bazarr": Unconfigured(), "sonarr": sonarr, "radarr": FakeRadarr()}
    bundle = SimpleNamespace(client_for=lambda svc, _iid: clients[svc])
    w = CompletionWatcher(provenance=SimpleNamespace(), bundle_provider=lambda: bundle)
    entry = SimpleNamespace(
        id=1,
        canonical_path="ShowTV/Season 1/ShowTV.S01E01.mkv",
        sonarr_episode_id=5,
        series_id=6,
        radarr_movie_id=None,
    )
    asyncio.run(w._fill_missing_arr_ids(entry))
    assert sonarr.series_calls == 0
