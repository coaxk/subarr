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
        if name in {"upsert_series_intent", "list_series_intents",
                    "delete_series_intent"}:
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
