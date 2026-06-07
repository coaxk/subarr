"""PUT /api/schedule/rules round-trip + a guard against the #117 bug class:
a new AutoQueueRules field that isn't mirrored in the RulesUpdate request model
is silently dropped by Pydantic and never persists.
"""
from __future__ import annotations


def test_rulesupdate_covers_every_autoqueue_field():
    """Every AutoQueueRules field must be accepted by RulesUpdate, else it
    can't be saved from the UI (settle_minutes hit exactly this — #117)."""
    from subarr.routers.schedule import RulesUpdate
    from subarr.schedule_store import AutoQueueRules

    rules_fields = set(AutoQueueRules.__dataclass_fields__)
    model_fields = set(RulesUpdate.model_fields)
    missing = rules_fields - model_fields
    assert not missing, (
        f"RulesUpdate is missing {sorted(missing)} — those fields silently "
        "drop on PUT /schedule/rules and never persist."
    )


def test_settle_minutes_persists_via_put(app_with_stub):
    c = app_with_stub
    r = c.put("/api/schedule/rules", json={"settle_minutes": 90})
    assert r.status_code == 200
    assert r.json()["settle_minutes"] == 90
    # round-trips through GET (i.e. actually written to the store)
    rules = c.get("/api/schedule").json()["rules"]
    assert rules["settle_minutes"] == 90


def test_queue_controls_persist_via_put(app_with_stub):
    c = app_with_stub
    c.put("/api/schedule/rules", json={"queue_target_depth": 4, "queue_paused": True})
    rules = c.get("/api/schedule").json()["rules"]
    assert rules["queue_target_depth"] == 4
    assert rules["queue_paused"] is True


def test_partial_update_preserves_other_fields(app_with_stub):
    c = app_with_stub
    c.put("/api/schedule/rules", json={"settle_minutes": 30})
    c.put("/api/schedule/rules", json={"min_score": 123})  # must not wipe settle_minutes
    rules = c.get("/api/schedule").json()["rules"]
    assert rules["settle_minutes"] == 30
    assert rules["min_score"] == 123
