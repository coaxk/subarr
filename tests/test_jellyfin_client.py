import pytest

from subarr.integrations.jellyfin import JellyfinClient
from subarr.integrations.media_server import MediaServer


def _c():
    return JellyfinClient(
        base_url="http://jf:8096", api_key="k", path_prefix="/media", media_root="/media/library"
    )


class _Resp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def test_conforms_to_protocol_and_type():
    c = _c()
    assert isinstance(c, MediaServer)
    assert c.type == "jellyfin"


def test_is_configured():
    assert _c().is_configured() is True
    assert JellyfinClient(base_url="", api_key="", path_prefix="", media_root="").is_configured() is False


def test_translate_path_applies_prefix():
    assert _c().translate_path("/media/library/TV/x.mkv") == "/media/TV/x.mkv"


@pytest.mark.asyncio
async def test_refresh_for_file_finds_item_and_refreshes(monkeypatch):
    c = _c()
    calls = []

    async def fake_request(method, path, params=None):
        calls.append((method, path, params))
        if path == "/Items":
            return _Resp({"Items": [{"Path": "/media/TV/x.mkv", "Id": "abc"}]})
        return _Resp({})

    monkeypatch.setattr(c, "_request", fake_request)
    out = await c.refresh_for_file("/media/library/TV/x.mkv")
    assert out["triggered"] is True and out["item_id"] == "abc"
    assert ("POST", "/Items/abc/Refresh", {"metadataRefreshMode": "Default"}) in calls


@pytest.mark.asyncio
async def test_refresh_for_file_no_match_is_noop(monkeypatch):
    c = _c()

    async def fake_request(method, path, params=None):
        return _Resp({"Items": []})

    monkeypatch.setattr(c, "_request", fake_request)
    out = await c.refresh_for_file("/media/library/TV/missing.mkv")
    assert out["triggered"] is False and out["reason"] == "no_item_match"


@pytest.mark.asyncio
async def test_full_refresh_posts_library_refresh(monkeypatch):
    c = _c()
    calls = []

    async def fake_request(method, path, params=None):
        calls.append((method, path))
        return _Resp({})

    monkeypatch.setattr(c, "_request", fake_request)
    out = await c.full_refresh()
    assert out["scope"] == "full" and ("POST", "/Library/Refresh") in calls


@pytest.mark.asyncio
async def test_status_reads_system_info(monkeypatch):
    c = _c()

    async def fake_request(method, path, params=None):
        return _Resp({"Version": "10.11.11", "ServerName": "JF"})

    monkeypatch.setattr(c, "_request", fake_request)
    assert (await c.status())["version"] == "10.11.11"


@pytest.mark.asyncio
async def test_auto_detects_prefix_when_explicit_empty(monkeypatch):
    # No explicit prefix; server library at /media/TV -> derive /media, match item, refresh
    c = JellyfinClient(base_url="http://jf:8096", api_key="k", path_prefix="", media_root="/media/library")
    calls = []

    async def fake_request(method, path, params=None):
        calls.append((method, path))
        if path == "/Library/VirtualFolders":
            return _Resp([{"Name": "Shows", "Locations": ["/media/TV"]}])
        if path == "/Items":
            return _Resp({"Items": [{"Path": "/media/TV/Show/ep.mkv", "Id": "id1"}]})
        return _Resp({})

    monkeypatch.setattr(c, "_request", fake_request)
    out = await c.refresh_for_file("/media/library/TV/Show/ep.mkv")
    assert out["triggered"] is True and out["item_id"] == "id1"


@pytest.mark.asyncio
async def test_explicit_prefix_wins_no_autodetect(monkeypatch):
    c = JellyfinClient(
        base_url="http://jf:8096", api_key="k", path_prefix="/media", media_root="/media/library"
    )
    fetched = {"libs": 0}

    async def fake_request(method, path, params=None):
        if path == "/Library/VirtualFolders":
            fetched["libs"] += 1
            return _Resp([])
        if path == "/Items":
            return _Resp({"Items": [{"Path": "/media/TV/Show/ep.mkv", "Id": "id1"}]})
        return _Resp({})

    monkeypatch.setattr(c, "_request", fake_request)
    out = await c.refresh_for_file("/media/library/TV/Show/ep.mkv")
    assert out["triggered"] is True
    assert fetched["libs"] == 0  # explicit prefix -> never fetched libraries


@pytest.mark.asyncio
async def test_effective_prefix_is_cached(monkeypatch):
    c = JellyfinClient(base_url="http://jf:8096", api_key="k", path_prefix="", media_root="/media/library")
    fetched = {"libs": 0}

    async def fake_request(method, path, params=None):
        if path == "/Library/VirtualFolders":
            fetched["libs"] += 1
            return _Resp([{"Name": "Shows", "Locations": ["/media/TV"]}])
        return _Resp({"Items": []})

    monkeypatch.setattr(c, "_request", fake_request)
    await c._effective_prefix("/media/library/TV/x.mkv")
    await c._effective_prefix("/media/library/TV/y.mkv")
    assert fetched["libs"] == 1  # derived once, cached
