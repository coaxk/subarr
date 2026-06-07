"""#66/#116 slice 3: pending-queue API — list, control (pause/target_depth),
reorder (promote/demote/move), remove, and the submitted-row guard.
"""
from __future__ import annotations

import pytest


def _enqueue(client, path, source="gaps"):
    return client.app.state.pending_queue.enqueue(path, source=source)


def test_list_pending_defaults(app_with_stub):
    c = app_with_stub
    _enqueue(c, "TV/a.mkv")
    r = c.get("/api/queue/pending")
    assert r.status_code == 200
    d = r.json()
    assert [j["canonical_path"] for j in d["pending"]] == ["TV/a.mkv"]
    assert d["paused"] is False
    assert d["target_depth"] == 2


def test_control_pause_and_depth_persist(app_with_stub):
    c = app_with_stub
    assert c.post("/api/queue/control", json={"paused": True}).json()["paused"] is True
    assert c.post("/api/queue/control", json={"target_depth": 5}).json()["target_depth"] == 5
    d = c.get("/api/queue/pending").json()
    assert d["paused"] is True and d["target_depth"] == 5


def test_control_clamps_negative_depth(app_with_stub):
    c = app_with_stub
    assert c.post("/api/queue/control", json={"target_depth": -3}).json()["target_depth"] == 0


def test_promote_reorders(app_with_stub):
    c = app_with_stub
    _enqueue(c, "TV/g1.mkv")
    _enqueue(c, "TV/g2.mkv")
    g3 = _enqueue(c, "TV/g3.mkv")
    c.post(f"/api/queue/pending/{g3.id}/promote")
    order = [j["canonical_path"] for j in c.get("/api/queue/pending").json()["pending"]]
    assert order[0] == "TV/g3.mkv"


def test_demote_reorders(app_with_stub):
    c = app_with_stub
    g1 = _enqueue(c, "TV/g1.mkv")
    _enqueue(c, "TV/g2.mkv")
    c.post(f"/api/queue/pending/{g1.id}/demote")
    order = [j["canonical_path"] for j in c.get("/api/queue/pending").json()["pending"]]
    assert order[-1] == "TV/g1.mkv"


def test_move_before(app_with_stub):
    c = app_with_stub
    _enqueue(c, "TV/g1.mkv")
    g2 = _enqueue(c, "TV/g2.mkv")
    g3 = _enqueue(c, "TV/g3.mkv")
    c.post(f"/api/queue/pending/{g3.id}/move", json={"before_id": g2.id})
    order = [j["canonical_path"] for j in c.get("/api/queue/pending").json()["pending"]]
    assert order == ["TV/g1.mkv", "TV/g3.mkv", "TV/g2.mkv"]


def test_move_needs_target(app_with_stub):
    c = app_with_stub
    a = _enqueue(c, "TV/a.mkv")
    assert c.post(f"/api/queue/pending/{a.id}/move", json={}).status_code == 400


def test_reorder_submitted_is_409(app_with_stub):
    c = app_with_stub
    a = _enqueue(c, "TV/a.mkv")
    c.app.state.pending_queue.mark_submitted(a.id)
    assert c.post(f"/api/queue/pending/{a.id}/promote").status_code == 409


def test_remove_pending(app_with_stub):
    c = app_with_stub
    a = _enqueue(c, "TV/a.mkv")
    assert c.delete(f"/api/queue/pending/{a.id}").json()["deleted"] is True
    assert c.get("/api/queue/pending").json()["pending"] == []


def test_unknown_id_404(app_with_stub):
    c = app_with_stub
    assert c.post("/api/queue/pending/nope/promote").status_code == 404
    assert c.delete("/api/queue/pending/nope").status_code == 404
