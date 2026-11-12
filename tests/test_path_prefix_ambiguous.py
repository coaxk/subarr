"""#662: path-prefix auto-detect refuses to guess between disagreeing libraries."""

import logging

import httpx
import pytest

from subarr.integrations.jellyfin import JellyfinClient
from subarr.integrations.media_server import derive_path_prefix
from subarr.integrations.plex import PlexClient

MEDIA_LOGGER = "subarr.integrations.media_server"


def _warnings(caplog):
    return [r for r in caplog.records if r.name == MEDIA_LOGGER and r.levelno == logging.WARNING]


def _capture(caplog):
    caplog.set_level(logging.WARNING, logger=MEDIA_LOGGER)
    return caplog


def test_reporter_case_anime_movies_first_refuses_to_pick(caplog):
    caplog = _capture(caplog)
    got = derive_path_prefix(
        ["/Media/Anime/Movies", "/Media/Movies", "/Media/TV", "/Media/Anime/TV"],
        "/Media",
        "/Media/Movies/X/x.mkv",
    )
    assert got is None
    assert len(_warnings(caplog)) == 0


def test_reporter_case_reversed_locations_refuses_to_pick(caplog):
    caplog = _capture(caplog)
    got = derive_path_prefix(
        ["/Media/Anime/TV", "/Media/TV", "/Media/Movies", "/Media/Anime/Movies"],
        "/Media",
        "/Media/Movies/X/x.mkv",
    )
    assert got is None
    assert len(_warnings(caplog)) == 0


def test_all_locations_agree_returns_shared_prefix(caplog):
    caplog = _capture(caplog)
    got = derive_path_prefix(["/data/TV", "/data/Movies"], "/media/library", "/media/library/TV/S/e.mkv")
    assert got == "/data"
    assert len(_warnings(caplog)) == 0


def test_duplicate_agreeing_candidates_return_shared_prefix(caplog):
    caplog = _capture(caplog)
    got = derive_path_prefix(["/data/TV", "/data/TV/"], "/media/library", "/media/library/TV/S/e.mkv")
    assert got == "/data"
    assert len(_warnings(caplog)) == 0


def test_two_disagreeing_candidates_warn_once_with_sorted_names(caplog):
    caplog = _capture(caplog)
    got = derive_path_prefix(
        ["/srv/Anime/Movies", "/data/Movies"], "/media/library", "/media/library/Movies/M/m.mkv"
    )
    assert got is None
    warns = _warnings(caplog)
    assert len(warns) == 1
    msg = warns[0].getMessage()
    assert "/data" in msg
    assert "/srv/Anime" in msg
    assert "Movies" in msg
    assert "PLEX_PATH_PREFIX" in msg
    assert "JELLYFIN_PATH_PREFIX" in msg
    assert msg.index("/data") < msg.index("/srv/Anime")


def test_reversed_disagreeing_candidates_warn_with_identical_text(caplog):
    caplog = _capture(caplog)
    first = derive_path_prefix(
        ["/srv/Anime/Movies", "/data/Movies"], "/media/library", "/media/library/Movies/M/m.mkv"
    )
    assert first is None
    first_msg = _warnings(caplog)[0].getMessage()
    caplog.clear()
    second = derive_path_prefix(
        ["/data/Movies", "/srv/Anime/Movies"], "/media/library", "/media/library/Movies/M/m.mkv"
    )
    assert second is None
    warns = _warnings(caplog)
    assert len(warns) == 1
    assert warns[0].getMessage() == first_msg


def test_majority_of_agreeing_candidates_is_still_ambiguous(caplog):
    caplog = _capture(caplog)
    got = derive_path_prefix(
        ["/data/Movies", "/data/Movies/", "/srv/Movies"], "/media/library", "/media/library/Movies/M/m.mkv"
    )
    assert got is None
    assert len(_warnings(caplog)) == 1


def test_identity_candidate_outranks_a_disagreeing_candidate(caplog):
    caplog = _capture(caplog)
    got = derive_path_prefix(
        ["/srv/Movies", "/media/library/Movies"], "/media/library", "/media/library/Movies/m.mkv"
    )
    assert got is None
    assert len(_warnings(caplog)) == 0


@pytest.mark.parametrize("locations", [["/Movies"], ["/Movies", "/data/Movies"]])
def test_empty_prefix_candidate_returns_none(caplog, locations):
    caplog = _capture(caplog)
    got = derive_path_prefix(locations, "/media/library", "/media/library/Movies/m.mkv")
    assert got is None
    assert len(_warnings(caplog)) == 0


def test_lookalike_suffix_is_not_a_candidate(caplog):
    caplog = _capture(caplog)
    assert derive_path_prefix(["/data/OldMovies"], "/media/library", "/media/library/Movies/m.mkv") is None
    assert len(_warnings(caplog)) == 0


def test_lookalike_suffix_does_not_block_a_real_candidate(caplog):
    caplog = _capture(caplog)
    got = derive_path_prefix(
        ["/data/OldMovies", "/srv/Movies"], "/media/library", "/media/library/Movies/m.mkv"
    )
    assert got == "/srv"
    assert len(_warnings(caplog)) == 0


def test_none_and_empty_locations_are_ignored(caplog):
    caplog = _capture(caplog)
    got = derive_path_prefix([None, "", "/data/TV"], "/media/library", "/media/library/TV/S/e.mkv")
    assert got == "/data"
    assert len(_warnings(caplog)) == 0


@pytest.mark.parametrize(
    ("media_root", "sample"),
    [
        ("/media/library", "/other/TV/e.mkv"),
        ("/media/library", "/media/library"),
        ("/media/library", "/media/library/"),
        ("", "/media/library/TV/e.mkv"),
    ],
)
def test_early_returns_log_nothing(caplog, media_root, sample):
    caplog = _capture(caplog)
    assert derive_path_prefix(["/data/TV"], media_root, sample) is None
    assert len(_warnings(caplog)) == 0


@pytest.mark.parametrize("locations", [[], ["/srv/Anime"]])
def test_no_candidates_returns_none(caplog, locations):
    caplog = _capture(caplog)
    got = derive_path_prefix(locations, "/media/library", "/media/library/TV/S/e.mkv")
    assert got is None
    assert len(_warnings(caplog)) == 0


# ── integration: clients that auto-derive the prefix ─────────────────────────


def _plex(media_root, paths, status=200):
    entries = "".join(
        f'<Directory key="{i}" title="lib{i}"><Location id="{i}" path="{p}"/></Directory>'
        for i, p in enumerate(paths, start=1)
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<MediaContainer size="{len(paths)}">{entries}</MediaContainer>'
    )

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/library/sections"
        return httpx.Response(status, text=xml)

    c = PlexClient(
        base_url="http://plex.test:32400",
        token="t",
        default_section="all",
        path_prefix="",
        media_root=media_root,
    )
    c._client = httpx.AsyncClient(base_url="http://plex.test:32400", transport=httpx.MockTransport(handler))
    return c


class _Resp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


@pytest.mark.asyncio
async def test_plex_reporter_case_falls_back_to_identity(caplog):
    caplog = _capture(caplog)
    c = _plex("/Media", ["/Media/Anime/Movies", "/Media/Movies"])
    prefix = await c._effective_prefix("/Media/Movies/X/x.mkv")
    assert prefix == ""
    assert c.translate_path("/Media/Movies/X/x.srt", prefix="") == "/Media/Movies/X/x.srt"
    assert len(_warnings(caplog)) == 0


@pytest.mark.asyncio
async def test_jellyfin_reporter_locations_in_one_library_fall_back_to_identity(caplog, monkeypatch):
    caplog = _capture(caplog)
    c = JellyfinClient(base_url="http://jf:8096", api_key="k", path_prefix="", media_root="/Media")

    async def fake_request(method, path, params=None):
        if path == "/Library/VirtualFolders":
            return _Resp([{"Name": "Movies", "Locations": ["/Media/Anime/Movies", "/Media/Movies"]}])
        return _Resp({"Items": []})

    monkeypatch.setattr(c, "_request", fake_request)
    assert await c._effective_prefix("/Media/Movies/X/x.mkv") == ""
    assert len(_warnings(caplog)) == 0


@pytest.mark.asyncio
async def test_ambiguous_result_is_cached_without_a_second_lookup_or_warning(caplog, monkeypatch):
    caplog = _capture(caplog)
    c = JellyfinClient(base_url="http://jf:8096", api_key="k", path_prefix="", media_root="/media/library")
    fetched = {"libs": 0}

    async def fake_request(method, path, params=None):
        if path == "/Library/VirtualFolders":
            fetched["libs"] += 1
            return _Resp([{"Name": "Movies", "Locations": ["/srv/Anime/Movies", "/data/Movies"]}])
        return _Resp({"Items": []})

    monkeypatch.setattr(c, "_request", fake_request)
    assert await c._effective_prefix("/media/library/Movies/M/m.mkv") == ""
    assert fetched["libs"] == 1
    assert len(_warnings(caplog)) == 1
    caplog.clear()
    assert await c._effective_prefix("/media/library/Movies/N/n.mkv") == ""
    assert fetched["libs"] == 1
    assert len(_warnings(caplog)) == 0


@pytest.mark.asyncio
async def test_plex_section_error_still_yields_identity():
    ok = _plex("/Media", ["/data/Movies"])
    assert await ok._effective_prefix("/Media/Movies/X/x.mkv") == "/data"
    c = _plex("/Media", ["/data/Movies"], status=500)
    assert await c._effective_prefix("/Media/Movies/X/x.mkv") == ""
