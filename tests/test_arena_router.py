"""#131 — arena API routes.

Route-level behaviour (capability gate, validation, create/list/get) against
the TestClient stub. The run *completion* path is proven at the service layer
(test_arena_service.py) — here we keep things deterministic and don't wait on
the background task to finish through the sync TestClient.
"""
from __future__ import annotations

import httpx
import pytest


def _arena_stub(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/status":
        return httpx.Response(200, json={"version": "Subgen 2026.05.3 (test)"})
    if req.url.path == "/queue":
        return httpx.Response(200, json={
            "queued": [], "processing": [],
            "capabilities": {"asr_arena": True},
        })
    if req.url.path == "/asr":
        return httpx.Response(200, text="1\n00:00:00,000 --> 00:00:02,000\nhi\n",
                              headers={"content-type": "text/plain"})
    return httpx.Response(404, json={"detail": "stub: unhandled"})


def _body(label="a", kwargs=None, path="TV/Show/ep.mkv"):
    return {"media_path": path, "variants": [{"label": label, "kwargs": kwargs or {}}]}


def test_by_language_route_returns_languages_shape(app_with_stub):
    # #26: route exists, returns the herd shape, and is NOT captured by /{run_id}.
    r = app_with_stub.get("/api/arena/by-language")
    assert r.status_code == 200
    assert isinstance(r.json().get("languages"), list)


def test_run_blocked_when_subgen_lacks_asr_arena(app_with_stub):
    # default stub /queue advertises no asr_arena → 503 with a clear reason
    r = app_with_stub.post("/api/arena/run", json=_body())
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "unsupported"
    assert "v4.10" in r.json()["detail"]["reason"]


@pytest.mark.subgen(handler=_arena_stub)
def test_run_created_and_listed(app_with_stub):
    r = app_with_stub.post("/api/arena/run", json=_body(kwargs={"beam_size": 5}))
    assert r.status_code == 202
    run = r.json()
    assert run["status"] in ("pending", "running", "done")
    assert run["variants"] == [{"label": "a", "kwargs": {"beam_size": 5}}]
    rid = run["id"]

    got = app_with_stub.get(f"/api/arena/{rid}")
    assert got.status_code == 200 and got.json()["id"] == rid

    listed = app_with_stub.get("/api/arena/runs").json()["runs"]
    assert any(x["id"] == rid for x in listed)


@pytest.mark.subgen(handler=_arena_stub)
def test_duplicate_variant_labels_rejected(app_with_stub):
    body = {"media_path": "TV/Show/ep.mkv",
            "variants": [{"label": "a", "kwargs": {}}, {"label": "a", "kwargs": {"x": 1}}]}
    r = app_with_stub.post("/api/arena/run", json=body)
    assert r.status_code == 400


@pytest.mark.subgen(handler=_arena_stub)
def test_unknown_path_404(app_with_stub):
    r = app_with_stub.post("/api/arena/run", json=_body(path="TV/Nope/missing.mkv"))
    assert r.status_code == 404


def test_get_unknown_run_404(app_with_stub):
    assert app_with_stub.get("/api/arena/deadbeef").status_code == 404


@pytest.mark.subgen(handler=_arena_stub)
def test_set_language_updates_run(app_with_stub):
    # Manual "set language" escape: sets the run's source_language (normalized)
    # so an undetermined sweep re-buckets in the herd.
    rid = app_with_stub.post("/api/arena/run", json=_body(kwargs={"beam_size": 5})).json()["id"]
    r = app_with_stub.post(f"/api/arena/{rid}/language", json={"lang": "nor"})
    assert r.status_code == 200
    assert r.json()["source_language"] == "no"          # nor → ISO-639-1 no
    # persisted: a fresh GET reflects it
    assert app_with_stub.get(f"/api/arena/{rid}").json()["source_language"] == "no"
    # unknown run → 404; undetermined-language input → 400
    assert app_with_stub.post("/api/arena/deadbeef/language", json={"lang": "no"}).status_code == 404
    assert app_with_stub.post(f"/api/arena/{rid}/language", json={"lang": "und"}).status_code == 400
