"""Regression: the series-intent CRUD routes must resolve at the single,
correctly-prefixed path `/api/audio-lang/series-intent`.

The router is declared with `prefix="/api/audio-lang"`, so the decorators must
use BARE paths (`/series-intent`). They previously repeated the prefix
(`@router.put("/audio-lang/series-intent")`), resolving to a double-prefixed
`/api/audio-lang/audio-lang/series-intent` — silently unreachable at the
intended path. These tests lock the route in place via both the live router
table and a real request through the app.
"""

from __future__ import annotations

SINGLE = "/api/audio-lang/series-intent"
DOUBLE = "/api/audio-lang/audio-lang/series-intent"


def _intent_methods(app):
    """Map HTTP method -> set of registered paths for the series-intent
    handlers, read straight off the app's route table."""
    by_method: dict[str, set[str]] = {}
    for r in app.routes:
        name = getattr(r, "name", "")
        if name in {"upsert_series_intent", "list_series_intents", "delete_series_intent"}:
            for m in getattr(r, "methods", set()):
                by_method.setdefault(m, set()).add(r.path)
    return by_method


def test_routes_registered_at_single_prefix(app_with_stub):
    by_method = _intent_methods(app_with_stub.app)
    assert by_method.get("PUT") == {SINGLE}
    assert by_method.get("GET") == {SINGLE}
    assert by_method.get("DELETE") == {SINGLE}
    # the double-prefixed path must not exist for any method
    assert all(DOUBLE not in paths for paths in by_method.values())


def test_get_resolves_at_single_path(app_with_stub):
    # GET at the correct path returns the (empty) list, not a 404.
    r = app_with_stub.get(SINGLE)
    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_double_prefixed_path_is_404(app_with_stub):
    assert app_with_stub.get(DOUBLE).status_code == 404


def test_put_then_get_roundtrips_at_single_path(app_with_stub):
    body = {"series_prefix": "TV/Cheers/", "lang_code": "eng"}
    r = app_with_stub.put(SINGLE, json=body)
    assert r.status_code == 200
    assert r.json()["series_prefix"] == "TV/Cheers/"

    items = app_with_stub.get(SINGLE).json()["items"]
    assert any(it.get("series_prefix") == "TV/Cheers/" for it in items)


class _RefreshSpy:
    """Stand-in for coverage_cache that records request_refresh calls and
    serves an empty snapshot."""

    def __init__(self):
        self.refresh_calls = 0

    def request_refresh(self, *args, **kwargs):
        self.refresh_calls += 1

    def get_cached(self):
        return None


def test_put_series_intent_kicks_coverage_refresh(app_with_stub):
    spy = _RefreshSpy()
    app_with_stub.app.state.coverage_cache = spy
    r = app_with_stub.put(SINGLE, json={"series_prefix": "TV/Cheers/", "lang_code": "eng"})
    assert r.status_code == 200
    assert spy.refresh_calls == 1


def test_delete_series_intent_kicks_coverage_refresh(app_with_stub):
    app_with_stub.app.state.audio_lang.set_series_intent(series_prefix="TV/Cheers/", lang_code="eng")
    spy = _RefreshSpy()
    app_with_stub.app.state.coverage_cache = spy
    r = app_with_stub.delete(SINGLE, params={"series_prefix": "TV/Cheers/"})
    assert r.status_code == 200
    assert r.json()["deleted"] is True
    assert spy.refresh_calls == 1


def test_delete_missing_series_intent_404(app_with_stub):
    r = app_with_stub.delete(SINGLE, params={"series_prefix": "TV/DoesNotExist/"})
    assert r.status_code == 404


class _SnapStub:
    def __init__(self, items):
        self.items = items


class _SnapCache:
    def __init__(self, items):
        self._snap = _SnapStub(items)

    def request_refresh(self, *a, **k):
        pass

    def get_cached(self):
        return self._snap


def test_get_series_intent_enriches_count_and_media_type(app_with_stub):
    store = app_with_stub.app.state.audio_lang
    store.set_series_intent(series_prefix="TV/Cheers/", lang_code="eng")
    store.set_series_intent(series_prefix="Movies/Parasite (2019)/", lang_code="kor")
    app_with_stub.app.state.coverage_cache = _SnapCache(
        [
            {"file_canonical_path": "TV/Cheers/Season 1/e1.mkv", "media_type": "episode"},
            {"file_canonical_path": "TV/Cheers/Season 1/e2.mkv", "media_type": "episode"},
            {"file_canonical_path": "Movies/Parasite (2019)/Parasite.mkv", "media_type": "movie"},
        ]
    )
    body = app_with_stub.get(SINGLE).json()
    by_prefix = {it["series_prefix"]: it for it in body["items"]}
    assert by_prefix["TV/Cheers/"]["covered_count"] == 2
    assert by_prefix["TV/Cheers/"]["media_type"] == "show"
    assert by_prefix["Movies/Parasite (2019)/"]["covered_count"] == 1
    assert by_prefix["Movies/Parasite (2019)/"]["media_type"] == "movie"


def test_get_series_intent_without_snapshot_returns_rules(app_with_stub):
    app_with_stub.app.state.audio_lang.set_series_intent(series_prefix="TV/Cheers/", lang_code="eng")
    app_with_stub.app.state.coverage_cache = _RefreshSpy()  # get_cached() -> None
    body = app_with_stub.get(SINGLE).json()
    assert body["items"][0]["series_prefix"] == "TV/Cheers/"
    assert body["items"][0]["covered_count"] == 0
    assert body["items"][0]["media_type"] == "show"
