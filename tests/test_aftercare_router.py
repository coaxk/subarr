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
    store.record(canonical_path="TV/A/e1.mkv", completed_at=1.0,
                 evaluation=AftercareEvaluation(40.0, 10, True, {"issues": []},
                                                {"canned_phrase_hits": 2}),
                 source="subgenscan")
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
    store.record(canonical_path="TV/A/e1.mkv", completed_at=1.0,
                 evaluation=AftercareEvaluation(40.0, 10, True, {"issues": []},
                                                {"canned_phrase_hits": 1}),
                 source="gaps")

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
    assert item["language"] == "ru"   # normalized from "Russian"
