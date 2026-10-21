"""#629: the manual enqueue paths (requeue, aftercare regenerate, Scan-tab
submit) queued with only the path, so the ledger row carried no Sonarr/Radarr
ids and completion could neither upload the .srt to Bazarr nor fire its
scan-disk fallback. Bazarr never heard about the regenerated subtitle, and
nothing was logged.

The fix resolves the ids at enqueue time from the coverage snapshot subarr
already holds (the same source #571 uses), and completion now says so at INFO
when it has to skip Bazarr for lack of ids.
"""

from __future__ import annotations

import logging

EP = "TV/Show/ep.mkv"
MOVIE = "Movies/Film (2020)/Film (2020).mkv"


class _Snap:
    def __init__(self, items):
        self.items = items


class _CC:
    def __init__(self, items):
        self._snap = _Snap(items)

    def get_cached(self):
        return self._snap


def _row(path, media_type="episode", **bazarr):
    return {"file_canonical_path": path, "media_type": media_type, "bazarr": bazarr}


# ── the lookup ───────────────────────────────────────────────────────


def test_an_episode_resolves_series_and_episode_ids():
    from subarr.coverage_cache import snapshot_enqueue_ids

    snap = _Snap([_row(EP, sonarr_id=2081, episode_id=74161)])
    assert snapshot_enqueue_ids(snap) == {EP: {"series_id": 2081, "sonarr_episode_id": 74161}}


def test_a_movie_resolves_its_radarr_id():
    from subarr.coverage_cache import snapshot_enqueue_ids

    snap = _Snap([_row(MOVIE, "movie", radarr_id=77)])
    assert snapshot_enqueue_ids(snap) == {MOVIE: {"radarr_movie_id": 77}}


def test_rows_without_a_file_or_an_id_resolve_to_nothing():
    from subarr.coverage_cache import snapshot_enqueue_ids

    absent = _row(EP, sonarr_id=1, episode_id=2)
    absent["file_canonical_path"] = None
    assert snapshot_enqueue_ids(_Snap([absent, _row(MOVIE, "movie")])) == {}
    assert snapshot_enqueue_ids(None) == {}


def test_enqueue_ids_for_tolerates_a_missing_or_broken_cache():
    from types import SimpleNamespace

    from subarr.coverage_cache import enqueue_ids_for

    assert enqueue_ids_for(SimpleNamespace(), [EP]) == {}

    class _Boom:
        def get_cached(self):
            raise RuntimeError("cache exploded")

    assert enqueue_ids_for(SimpleNamespace(coverage_cache=_Boom()), [EP]) == {}


# ── the three call sites ─────────────────────────────────────────────


def _hold_feeder(app, paused: bool) -> None:
    rules = app.state.schedule.get_rules()
    rules.queue_paused = paused
    app.state.schedule.set_rules(rules)


def test_requeue_carries_the_arr_ids_onto_the_job(app_with_stub):
    app = app_with_stub.app
    app.state.coverage_cache = _CC([_row(EP, sonarr_id=2081, episode_id=74161)])
    _hold_feeder(app, True)
    try:
        r = app_with_stub.post("/api/queue/requeue", json={"path": EP})
        job = app.state.pending_queue.get(r.json()["job"])
        assert (job.series_id, job.sonarr_episode_id, job.radarr_movie_id) == (2081, 74161, None)
    finally:
        _hold_feeder(app, False)


def test_scan_submit_carries_the_arr_ids_onto_the_job(app_with_stub):
    app = app_with_stub.app
    app.state.coverage_cache = _CC([_row(EP, sonarr_id=2081, episode_id=74161)])
    _hold_feeder(app, True)
    try:
        r = app_with_stub.post("/api/scan", json={"paths": [EP]})
        assert r.status_code in (200, 202), r.text
        job = app.state.pending_queue.get(r.json()["jobs"][0])
        assert (job.series_id, job.sonarr_episode_id) == (2081, 74161)
    finally:
        _hold_feeder(app, False)


def test_regenerate_carries_the_arr_ids_onto_the_job(tmp_path, monkeypatch):
    from pathlib import Path

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from subarr.aftercare import AftercareEvaluation
    from subarr.aftercare_store import AfterCareStore
    from subarr.migrate import run_migrations
    from subarr.routers import aftercare as r

    db = tmp_path / "a.db"
    run_migrations(db)
    store = AfterCareStore(db)
    store.record(
        canonical_path="Movies/Film (2020)/Film (2020).en.srt",
        completed_at=1.0,
        evaluation=AftercareEvaluation(40.0, 10, True, {"issues": []}, {}),
        source="existing_audit",
    )
    enqueued = []

    class _Pending:
        def enqueue(self, canonical, **kw):
            enqueued.append((canonical, kw))
            return type("J", (), {"id": "job-1"})()

    app = FastAPI()
    app.state.aftercare = store
    app.state.pending_queue = _Pending()
    app.state.queue_feeder = type("F", (), {"kick": lambda self: None})()
    app.state.audio_lang = None
    app.state.coverage_cache = _CC([_row(MOVIE, "movie", radarr_id=77)])
    app.include_router(r.router)
    monkeypatch.setattr(r, "resolve_media_for_srt", lambda p: Path("/m/" + MOVIE))
    monkeypatch.setattr(r, "fs_to_canonical", lambda p: MOVIE)
    client = TestClient(app)
    rid = client.get("/api/aftercare/results?view=flagged").json()["items"][0]["id"]

    assert client.post(f"/api/aftercare/{rid}/regenerate").status_code == 202
    assert enqueued == [(MOVIE, {"source": "manual", "audio_language_override": None, "radarr_movie_id": 77})]


# ── completion: a skip is never silent again ─────────────────────────


def test_completion_logs_when_bazarr_is_skipped_for_lack_of_ids(writeback_stack, caplog):
    import asyncio
    from pathlib import Path
    from types import SimpleNamespace

    from subarr.completion_watcher import CompletionWatcher
    from subarr.paths import canonical_to_fs

    canonical = "ShowTV/Season 1/ShowTV.S01E01.mkv"
    fs = Path(canonical_to_fs(canonical))
    fs.parent.mkdir(parents=True, exist_ok=True)
    fs.write_bytes(b"\x00")
    (fs.parent / (fs.stem + ".en.srt")).write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n")
    prov = SimpleNamespace(mark_bazarr_triggered=lambda _id: None)
    w = CompletionWatcher(provenance=prov, bundle_provider=lambda: writeback_stack.bundle)
    entry = SimpleNamespace(
        id=2201, canonical_path=canonical, sonarr_episode_id=None, series_id=None, radarr_movie_id=None
    )

    with caplog.at_level(logging.INFO, logger="subarr.completion_watcher"):
        assert asyncio.run(w._try_upload_to_bazarr(entry)) is False
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]
    assert any("no Sonarr/Radarr ids" in m and "#2201" in m for m in msgs), msgs
