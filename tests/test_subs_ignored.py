"""#316: per-title 'ignore / I don't want subs here'.

A deliberate user exclude (distinct from #311 detection bugs): suppress gap
flagging for a whole title — a movie file, or a TV series prefix. Modeled on the
existing mixed-series / track-mismatch dismiss stores.
"""

from __future__ import annotations

_MOVIE = "movies/101 Dalmatians (1996)/101.Dalmatians.1996.mkv"
_SERIES = "TV/Some Dutch Show/"  # series prefix (trailing slash, like series-intent)


def test_ignore_roundtrip_and_set(app_with_stub):
    store = app_with_stub.app.state.audio_lang
    store.ignore_title(_MOVIE, note="dutch, no eng subs wanted")
    store.ignore_title(_SERIES)

    s = store.get_ignored_titles_set()
    assert _MOVIE in s and _SERIES in s

    # idempotent — re-ignoring the same path doesn't duplicate
    store.ignore_title(_SERIES)
    assert len(store.get_ignored_titles_set()) == 2

    listed = store.list_ignored_titles()
    assert any(r["path"] == _MOVIE and r["note"] == "dutch, no eng subs wanted" for r in listed)

    assert store.unignore_title(_SERIES) is True
    assert _SERIES not in store.get_ignored_titles_set()
    assert store.unignore_title(_SERIES) is False  # already gone


def test_drop_ignored_titles_helper():
    """The suppression: a movie file matches exactly; a series prefix drops every
    episode beneath it; unrelated titles survive."""
    from subarr.coverage_engine import CoverageItem, _drop_ignored_titles

    items = [
        CoverageItem(media_type="movie", title="101 Dalmatians", file_canonical_path=_MOVIE),
        CoverageItem(
            media_type="episode",
            title="Some Dutch Show",
            file_canonical_path="TV/Some Dutch Show/Season 1/e01.mkv",
        ),
        CoverageItem(
            media_type="episode",
            title="Keeper",
            file_canonical_path="TV/Keeper/Season 1/e01.mkv",
        ),
    ]
    kept, dropped = _drop_ignored_titles(items, {_MOVIE, _SERIES})
    assert dropped == 2
    assert {i.title for i in kept} == {"Keeper"}

    # no ignores → untouched (and cheap)
    kept2, dropped2 = _drop_ignored_titles(items, set())
    assert dropped2 == 0 and len(kept2) == 3


def test_ignore_endpoints_roundtrip(app_with_stub):
    c = app_with_stub
    r = c.post("/api/coverage/ignore-title", json={"path": _MOVIE, "note": "dutch"})
    assert r.status_code == 200 and r.json()["ignored"] is True

    listed = c.get("/api/coverage/ignored").json()["ignored"]
    assert any(x["path"] == _MOVIE and x["note"] == "dutch" for x in listed)

    # empty path rejected
    assert c.post("/api/coverage/ignore-title", json={"path": "  "}).status_code == 400

    r = c.request("DELETE", "/api/coverage/ignore-title", params={"path": _MOVIE})
    assert r.status_code == 200
    assert not any(x["path"] == _MOVIE for x in c.get("/api/coverage/ignored").json()["ignored"])

    # un-ignoring something not ignored → 404
    r = c.request("DELETE", "/api/coverage/ignore-title", params={"path": _MOVIE})
    assert r.status_code == 404
