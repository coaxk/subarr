"""#156 aftercare router."""

from __future__ import annotations

from typing import ClassVar

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBARR_DB_PATH", str(tmp_path / "a.db"))
    from fastapi import FastAPI

    from subarr.aftercare import AftercareEvaluation
    from subarr.aftercare_store import AfterCareStore
    from subarr.migrate import run_migrations
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


# ── server-side search + pagination regression coverage ─────────────────────


def _results_client(tmp_path, *, n=5):
    """Build an aftercare router app with `n` flagged rows, for search/pagination
    assertions that need more than the single-row `client` fixture."""
    from fastapi import FastAPI

    from subarr.aftercare import AftercareEvaluation
    from subarr.aftercare_store import AfterCareStore
    from subarr.migrate import run_migrations
    from subarr.routers import aftercare as r

    db = tmp_path / "a.db"
    run_migrations(db)
    store = AfterCareStore(db)
    for i in range(n):
        store.record(
            canonical_path=f"TV/A/e{i}.mkv",
            completed_at=1.0 + i,
            evaluation=AftercareEvaluation(40.0, 10, True, {"issues": []}, {}),
            source="subgenscan",
        )
    app = FastAPI()
    app.state.aftercare = store
    app.include_router(r.router)
    return TestClient(app)


def test_results_validates_query_params(client):
    assert client.get("/api/aftercare/results?limit=0").status_code == 422
    assert client.get("/api/aftercare/results?limit=501").status_code == 422
    assert client.get("/api/aftercare/results?offset=-1").status_code == 422
    assert client.get("/api/aftercare/results?view=bogus").status_code == 422
    assert client.get("/api/aftercare/results", params={"search": "x" * 201}).status_code == 422
    # empty search string is allowed
    assert client.get("/api/aftercare/results?search=").status_code == 200


def test_results_search_forwarded(client):
    # matching search still returns the row
    body = client.get("/api/aftercare/results?search=e1").json()
    assert body["count"] == 1
    assert body["items"][0]["canonical_path"] == "TV/A/e1.mkv"
    # missing search -> zero items, truthful zero count
    body = client.get("/api/aftercare/results?search=zzz").json()
    assert body["count"] == 0
    assert body["items"] == []
    # empty search string is forwarded and treated as no filter
    body = client.get("/api/aftercare/results?search=&limit=10&offset=0").json()
    assert body["count"] == 1


def test_results_count_is_total_not_page_length(tmp_path):
    client = _results_client(tmp_path, n=5)
    body = client.get("/api/aftercare/results?limit=2&offset=0").json()
    assert body["count"] == 2  # compatibility: count is the returned page length
    assert body["total"] == 5  # truthful total, not the page length
    assert len(body["items"]) == 2  # page length
    body = client.get("/api/aftercare/results?limit=2&offset=4").json()
    assert body["count"] == 1
    assert body["total"] == 5
    assert len(body["items"]) == 1  # tail page
    # search narrows count AND page together
    body = client.get("/api/aftercare/results?search=e3").json()
    assert body["count"] == 1
    assert body["items"][0]["canonical_path"] == "TV/A/e3.mkv"
    # offset past the matching set -> empty items, truthful count
    body = client.get("/api/aftercare/results?search=e&limit=2&offset=10").json()
    assert body["count"] == 0
    assert body["total"] == 5
    assert body["items"] == []


def test_results_crosses_historical_page_boundary_without_loss(tmp_path):
    client = _results_client(tmp_path, n=150)
    first = client.get("/api/aftercare/results", params={"limit": 100, "offset": 0}).json()
    second = client.get("/api/aftercare/results", params={"limit": 100, "offset": 100}).json()

    assert first["total"] == second["total"] == 150
    paths = [item["canonical_path"] for item in first["items"] + second["items"]]
    assert len(paths) == 150
    assert len(set(paths)) == 150


def test_results_search_crosses_historical_page_boundary(tmp_path):
    client = _results_client(tmp_path, n=150)
    body = client.get("/api/aftercare/results", params={"search": "e149", "limit": 50}).json()

    assert body["total"] == 1
    assert body["items"][0]["canonical_path"] == "TV/A/e149.mkv"


def test_results_search_still_enriches_language_and_library(tmp_path, monkeypatch):
    # enrichment (language from coverage snapshot + library label) must still be
    # applied to the returned page when search/pagination are in play.
    monkeypatch.setenv("SUBARR_DB_PATH", str(tmp_path / "a.db"))
    from fastapi import FastAPI

    from subarr.aftercare import AftercareEvaluation
    from subarr.aftercare_store import AfterCareStore
    from subarr.migrate import run_migrations
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
        items: ClassVar = [{"file_canonical_path": "TV/A/e1.mkv", "original_language": "Russian"}]

    class _CC:
        def get_cached(self):
            return _Snap()

    app = FastAPI()
    app.state.aftercare = store
    app.state.coverage_cache = _CC()
    app.include_router(r.router)
    client = TestClient(app)
    item = client.get("/api/aftercare/results?search=e1").json()["items"][0]
    assert item["language"] == "ru"  # normalized from "Russian"
    assert "library" in item  # library provenance still present


def test_acknowledge_all_router(client):
    # bulk-acknowledge endpoint semantics are unchanged by search/pagination.
    assert client.get("/api/aftercare/pending").json() == {"count": 1}
    body = client.post("/api/aftercare/acknowledge-all").json()
    assert body["ok"] is True
    assert body["acknowledged"] == 1
    assert client.get("/api/aftercare/pending").json() == {"count": 0}
    # idempotent — nothing left pending
    assert client.post("/api/aftercare/acknowledge-all").json()["acknowledged"] == 0


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

    from subarr.aftercare import AftercareEvaluation
    from subarr.aftercare_store import AfterCareStore
    from subarr.migrate import run_migrations
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
        items: ClassVar = [{"file_canonical_path": "TV/A/e1.mkv", "original_language": "Russian"}]

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

    app, _store = _regen_app(tmp_path, source="existing_audit")
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
