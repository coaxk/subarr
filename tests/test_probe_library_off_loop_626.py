"""#626: GET /api/probe/library must not hydrate the probe cache on the event loop.

Measured 2026-11-02 at 40,000 media_probe rows with 2 CPUs: the route ran
all_entries() on the loop and blocked it for 1.1-1.5 s per call, logging a
loop_lag STALLED warning on every call. The coverage refresh, which already ran
the same hydration in a worker thread, stayed under the 0.5 s threshold.

These tests pin that the hydration AND the per-row classification run in a
worker thread, plus the filters and limit that had no test before the move.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path


def _seed_probe(app_with_stub, canonical: str, sub_kind: str = "full"):
    # Reuse the existing suite's seeding helper (tests/ is not a package).
    spec = importlib.util.spec_from_file_location(
        "_library_probe_helpers_626", Path(__file__).with_name("test_library_probe.py")
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m._seed_probe(app_with_stub, canonical, sub_kind)


def _on_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def test_hydration_and_classification_run_off_the_event_loop(app_with_stub, monkeypatch):
    import subarr.media_probe as mp

    _seed_probe(app_with_stub, "TV/Alpha/Alpha.S01E01.mkv", "full")
    store = app_with_stub.app.state.probe_store

    hydrate_calls: list[bool] = []
    real_all = store.all_entries

    def spy_all():
        hydrate_calls.append(_on_loop())
        return real_all()

    monkeypatch.setattr(store, "all_entries", spy_all)

    classify_calls: list[bool] = []
    real_summary = mp.english_track_summary

    def spy_summary(r):
        classify_calls.append(_on_loop())
        return real_summary(r)

    monkeypatch.setattr(mp, "english_track_summary", spy_summary)

    r = app_with_stub.get("/api/probe/library")
    assert r.status_code == 200
    assert r.json()["shown"] == 1
    # The app's background coverage refresh may also hydrate (always from a
    # worker thread), so assert "never on the loop" rather than an exact list.
    assert hydrate_calls, "the route must hydrate the probe cache"
    assert True not in hydrate_calls, "all_entries() must run in a worker thread, not on the loop"
    assert classify_calls, "the route must classify each row"
    assert True not in classify_calls, "per-row classification must run in a worker thread too"


def test_limit_caps_shown_but_total_counts_every_row(app_with_stub):
    for i in range(5):
        _seed_probe(app_with_stub, f"TV/Show{i}/Show{i}.mkv", "full")
    body = app_with_stub.get("/api/probe/library?limit=2").json()
    assert body["total_cached"] == 5
    assert body["shown"] == 2
    assert len(body["items"]) == 2


def test_filter_only_without_eng_excludes_partial_english(app_with_stub):
    _seed_probe(app_with_stub, "TV/Full/Full.mkv", "full")
    _seed_probe(app_with_stub, "TV/Forced/Forced.mkv", "forced")
    _seed_probe(app_with_stub, "TV/Nope/Nope.mkv", "none")
    body = app_with_stub.get("/api/probe/library?only_without_eng=true").json()
    assert [i["canonical_path"].split("/")[1] for i in body["items"]] == ["Nope"]
    assert body["total_cached"] == 3


def test_filters_combine_and_empty_store_is_fine(app_with_stub):
    body = app_with_stub.get("/api/probe/library").json()
    assert body == {"total_cached": 0, "shown": 0, "items": []}

    _seed_probe(app_with_stub, "TV/Alpha Show/a.mkv", "forced")
    _seed_probe(app_with_stub, "TV/Alpha Other/b.mkv", "sdh")
    _seed_probe(app_with_stub, "TV/Beta/c.mkv", "forced")
    body = app_with_stub.get("/api/probe/library?filter_text=ALPHA&sub_kind=forced").json()
    assert [i["canonical_path"] for i in body["items"]] == ["TV/Alpha Show/a.mkv"]
    item = body["items"][0]
    assert item["subtitle_streams"][0]["forced"] is True
    assert item["english_track"] == "EN(forced)"
