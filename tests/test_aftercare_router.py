"""#156 aftercare router."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBARR_DB_PATH", str(tmp_path / "a.db"))
    from fastapi import FastAPI
    from subarr.migrate import run_migrations
    from subarr.aftercare_store import AfterCareStore
    from subarr.aftercare import AftercareEvaluation
    from subarr.routers import aftercare as r

    db = tmp_path / "a.db"
    run_migrations(db)
    store = AfterCareStore(db)
    store.record(
        canonical_path="TV/A/e1.mkv",
        completed_at=1.0,
        evaluation=AftercareEvaluation(40.0, 10, True, {"issues": []}, {"canned_phrase_hits": 2}),
        source="subgenscan",
    )
    app = FastAPI()
    app.state.aftercare = store
    app.include_router(r.router)
    return TestClient(app)


def test_pending(client):
    assert client.get("/api/aftercare/pending").json() == {"count": 1}


def test_results_flagged(client):
    body = client.get("/api/aftercare/results?view=flagged").json()
    assert body["count"] == 1
    assert body["items"][0]["canonical_path"] == "TV/A/e1.mkv"
    assert body["items"][0]["flagged"] is True


def test_acknowledge(client):
    rid = client.get("/api/aftercare/results?view=flagged").json()["items"][0]["id"]
    assert client.post(f"/api/aftercare/{rid}/acknowledge").json()["ok"] is True
    assert client.get("/api/aftercare/pending").json() == {"count": 0}
    # idempotent: re-acking an existing (already-reviewed) id is OK, not 404
    assert client.post(f"/api/aftercare/{rid}/acknowledge").status_code == 200
    # 404 only for a genuinely absent id
    assert client.post("/api/aftercare/999999/acknowledge").status_code == 404


def test_results_enriches_language_from_coverage(tmp_path, monkeypatch):
    # results enriches each row with the show's language from the coverage
    # snapshot (best-effort), normalized to an ISO code.
    monkeypatch.setenv("SUBARR_DB_PATH", str(tmp_path / "a.db"))
    from fastapi import FastAPI
    from subarr.migrate import run_migrations
    from subarr.aftercare_store import AfterCareStore
    from subarr.aftercare import AftercareEvaluation
    from subarr.routers import aftercare as r

    db = tmp_path / "a.db"
    run_migrations(db)
    store = AfterCareStore(db)
    store.record(
        canonical_path="TV/A/e1.mkv",
        completed_at=1.0,
        evaluation=AftercareEvaluation(40.0, 10, True, {"issues": []}, {"canned_phrase_hits": 1}),
        source="gaps",
    )

    class _Snap:
        items = [{"file_canonical_path": "TV/A/e1.mkv", "original_language": "Russian"}]

    class _CC:
        def get_cached(self):
            return _Snap()

    app = FastAPI()
    app.state.aftercare = store
    app.state.coverage_cache = _CC()
    app.include_router(r.router)
    client = TestClient(app)
    item = client.get("/api/aftercare/results?view=flagged").json()["items"][0]
    assert item["language"] == "ru"  # normalized from "Russian"


# ── #216 regenerate-from-audio (existing-audit rows) ──────────────────────────


class _FakeJob:
    id = "job-1"


class _FakePending:
    def __init__(self):
        self.enqueued = []

    def enqueue(self, canonical, *, source, audio_language_override=None):
        self.enqueued.append(canonical)
        return _FakeJob()


class _FakeFeeder:
    def __init__(self):
        self.kicked = 0

    def kick(self):
        self.kicked += 1


def _regen_app(tmp_path, *, source, srt_rel="TV/A/e1.en.srt"):
    from fastapi import FastAPI
    from subarr.aftercare import AftercareEvaluation
    from subarr.aftercare_store import AfterCareStore
    from subarr.migrate import run_migrations
    from subarr.routers import aftercare as r

    db = tmp_path / "a.db"
    run_migrations(db)
    store = AfterCareStore(db)
    store.record(
        canonical_path=srt_rel,
        completed_at=1.0,
        evaluation=AftercareEvaluation(40.0, 10, True, {"issues": []}, {}),
        source=source,
    )
    app = FastAPI()
    app.state.aftercare = store
    app.state.pending_queue = _FakePending()
    app.state.queue_feeder = _FakeFeeder()
    app.state.audio_lang = None
    app.include_router(r.router)
    return app, store


def test_regenerate_resolves_video_and_enqueues(tmp_path, monkeypatch):
    from pathlib import Path

    from subarr.routers import aftercare as r

    app, store = _regen_app(tmp_path, source="existing_audit")
    # avoid real fs/config: srt -> sibling video -> canonical
    monkeypatch.setattr(r, "resolve_media_for_srt", lambda p: Path("/m/TV/A/e1.mkv"))
    monkeypatch.setattr(r, "fs_to_canonical", lambda p: "TV/A/e1.mkv")
    client = TestClient(app)
    rid = client.get("/api/aftercare/results?view=flagged").json()["items"][0]["id"]

    resp = client.post(f"/api/aftercare/{rid}/regenerate")
    assert resp.status_code == 202
    assert resp.json()["path"] == "TV/A/e1.mkv"  # the VIDEO, not the .srt
    assert app.state.pending_queue.enqueued == ["TV/A/e1.mkv"]
    assert app.state.queue_feeder.kicked == 1


def test_regenerate_rejects_generated_rows(tmp_path):
    app, _ = _regen_app(tmp_path, source="subgenscan", srt_rel="TV/A/e1.mkv")
    client = TestClient(app)
    rid = client.get("/api/aftercare/results?view=flagged").json()["items"][0]["id"]
    assert client.post(f"/api/aftercare/{rid}/regenerate").status_code == 400


def test_regenerate_404_for_missing_row(tmp_path):
    app, _ = _regen_app(tmp_path, source="existing_audit")
    assert TestClient(app).post("/api/aftercare/999999/regenerate").status_code == 404


def test_regenerate_422_when_no_sibling_video(tmp_path, monkeypatch):
    from subarr.routers import aftercare as r

    app, _ = _regen_app(tmp_path, source="existing_audit")
    monkeypatch.setattr(r, "resolve_media_for_srt", lambda p: None)
    client = TestClient(app)
    rid = client.get("/api/aftercare/results?view=flagged").json()["items"][0]["id"]
    assert client.post(f"/api/aftercare/{rid}/regenerate").status_code == 422
