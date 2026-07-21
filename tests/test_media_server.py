import pytest

from subarr.integrations.media_server import MediaServer
from subarr.integrations.plex import PlexClient


def _client():
    return PlexClient(
        base_url="http://plex:32400",
        token="t",
        default_section="all",
        path_prefix="",
        media_root="/media/library",
    )


def test_plex_client_satisfies_media_server_protocol():
    c = _client()
    assert isinstance(c, MediaServer)  # runtime_checkable structural check
    assert c.type == "plex"


@pytest.mark.asyncio
async def test_refresh_for_file_delegates_to_partial_scan(monkeypatch):
    c = _client()
    calls = {}

    async def fake_partial(p):
        calls["path"] = p
        return {"triggered": True, "scope": "partial"}

    monkeypatch.setattr(c, "partial_scan", fake_partial)
    out = await c.refresh_for_file("/media/library/TV/x.mkv")
    assert calls["path"] == "/media/library/TV/x.mkv" and out["triggered"] is True


@pytest.mark.asyncio
async def test_full_refresh_delegates_to_full_scan(monkeypatch):
    c = _client()

    async def fake_full():
        return {"triggered": True, "scope": "full"}

    monkeypatch.setattr(c, "full_scan", fake_full)
    out = await c.full_refresh()
    assert out["scope"] == "full"
