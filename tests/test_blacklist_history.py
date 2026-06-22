"""#317 Slice A: surface a file's Bazarr subtitle-download history so the user
can blacklist a bad provider sub. Schema verified against a live Bazarr
2026-06-22: each row carries sonarrSeriesId/sonarrEpisodeId/provider/subs_id/
language{code2}/subtitles_path/blacklisted; manual uploads have subs_id=null
(not blacklistable).
"""

from __future__ import annotations

import httpx
import pytest

# A representative downloaded-from-provider row (blacklistable) + a manual
# upload (subs_id null → not) + an already-blacklisted one.
_PROVIDER_ROW = {
    "provider": "opensubtitles",
    "subs_id": "abc123",
    "subtitles_path": "/media/TV/Show/S01E01.en.srt",
    "language": {"name": "English", "code2": "en", "code3": "eng", "forced": False, "hi": False},
    "score": 98,
    "blacklisted": False,
    "action": 1,
    "sonarrSeriesId": 7,
    "sonarrEpisodeId": 42,
    "description": "opensubtitles English",
    "timestamp": "2 days ago",
}
_MANUAL_ROW = {**_PROVIDER_ROW, "provider": "manual", "subs_id": None, "action": 4}
_ALREADY_BL = {**_PROVIDER_ROW, "subs_id": "def456", "blacklisted": True}


def test_shape_history_row_marks_blacklistable():
    from subarr.routers.blacklist import _shape_history_row

    good = _shape_history_row(_PROVIDER_ROW, "episode")
    assert good["blacklistable"] is True
    assert good["provider"] == "opensubtitles"
    assert good["subs_id"] == "abc123"
    assert good["language"] == "en"
    assert good["series_id"] == 7 and good["episode_id"] == 42

    # manual upload: no subs_id → can't be blacklisted
    assert _shape_history_row(_MANUAL_ROW, "episode")["blacklistable"] is False
    # already blacklisted → not offered again
    assert _shape_history_row(_ALREADY_BL, "episode")["blacklistable"] is False


def _bazarr_history_stub(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/api/episodes/history":
        return httpx.Response(200, json={"data": [_PROVIDER_ROW, _MANUAL_ROW, _ALREADY_BL]})
    if req.url.path == "/api/system/status":
        return httpx.Response(200, json={"data": {"bazarr_version": "1.4"}})
    return httpx.Response(404, json={"detail": "stub"})


@pytest.mark.integrations_stub(bazarr_handler=_bazarr_history_stub)
def test_history_endpoint_shapes_rows(app_with_stub):
    r = app_with_stub.get("/api/blacklist/history?media_type=episode&id=42")
    assert r.status_code == 200
    subs = r.json()["subtitles"]
    assert len(subs) == 3
    assert sum(1 for s in subs if s["blacklistable"]) == 1


def test_history_endpoint_rejects_bad_media_type(app_with_stub):
    # FastAPI Query(pattern=...) rejects an out-of-set media_type as 422.
    assert app_with_stub.get("/api/blacklist/history?media_type=show&id=1").status_code == 422
