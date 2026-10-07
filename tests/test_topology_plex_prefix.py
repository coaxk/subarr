"""#598: Settings > Libraries showed a false "no Plex section" warning when
Plex sees the media tree under a different path (PLEX_PATH_PREFIX).

The topology lookup handed subarr's own fs_root to Plex's section matcher
untranslated, so /media/library/movies was compared against /data/movies.
Partial scan already translated; topology now does the same.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from types import SimpleNamespace

import httpx
import pytest

from subarr.integrations.plex import PlexClient
from subarr.routers.instances import _plex_section_for

SECTIONS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<MediaContainer size="3">
  <Directory key="1" title="Movies"><Location id="1" path="{root}/movies"/></Directory>
  <Directory key="2" title="TV"><Location id="2" path="{root}/tv_shows"/></Directory>
  <Directory key="3" title="Anime"><Location id="3" path="{root}/tv_shows_anime"/></Directory>
</MediaContainer>
"""


def _plex(plex_root: str, path_prefix: str) -> PlexClient:
    xml = SECTIONS_XML.format(root=plex_root)

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/library/sections"
        return httpx.Response(200, text=xml)

    c = PlexClient(
        base_url="http://plex.test:32400",
        token="t",
        default_section="all",
        path_prefix=path_prefix,
        media_root="/media/library",
    )
    c._client = httpx.AsyncClient(base_url="http://plex.test:32400", transport=httpx.MockTransport(handler))
    return c


def _req(plex):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(integrations=SimpleNamespace(plex=plex)))
    )


def _lib(path: str):
    return SimpleNamespace(slug=path.rsplit("/", 1)[-1], fs_root=PurePosixPath(path))


CHILDREN = {
    "/media/library/movies": "1",
    "/media/library/tv_shows": "2",
    "/media/library/tv_shows_anime": "3",
}


async def _match_all(plex, paths):
    req = _req(plex)
    return {p: await _plex_section_for(req, _lib(p)) for p in paths}


@pytest.mark.asyncio
async def test_explicit_prefix_that_differs_matches_children():
    """The reporter's setup: SUBARR_MEDIA_ROOT=/media/library, PLEX_PATH_PREFIX=/data."""
    got = await _match_all(_plex("/data", "/data"), CHILDREN)
    for path, sid in CHILDREN.items():
        assert got[path] == {"name": sid, "matched": True}, path


@pytest.mark.asyncio
async def test_auto_detected_prefix_matches_children():
    got = await _match_all(_plex("/data", ""), CHILDREN)
    for path, sid in CHILDREN.items():
        assert got[path] == {"name": sid, "matched": True}, path


@pytest.mark.asyncio
async def test_identity_mapping_still_matches():
    got = await _match_all(_plex("/media/library", ""), CHILDREN)
    for path, sid in CHILDREN.items():
        assert got[path] == {"name": sid, "matched": True}, path


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["/data", ""])
async def test_parent_root_stays_unmatched(prefix):
    """Plex sections start at the CHILD folders, so the parent warning is real."""
    got = await _match_all(_plex("/data", prefix), ["/media/library"])
    assert got["/media/library"] == {"name": None, "matched": False}


@pytest.mark.asyncio
async def test_parent_root_first_does_not_poison_auto_prefix():
    """Topology walks the default (parent) library FIRST. A prefix cannot be
    derived from the bare media root, and caching that miss as identity would
    unmatch every child here and break auto-detected partial scans after it."""
    plex = _plex("/data", "")
    got = await _match_all(plex, ["/media/library", *CHILDREN])
    assert got["/media/library"]["matched"] is False
    for path, sid in CHILDREN.items():
        assert got[path] == {"name": sid, "matched": True}, path
    assert await plex._effective_prefix("/media/library/movies/X.mkv") == "/data"
