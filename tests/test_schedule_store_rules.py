"""ScheduleStore rules persistence.

Regression guard for the init_schema removal: auto_queue_rules is no
longer seeded at boot (get_rules() defaults when the row is absent), so
set_rules() MUST upsert — an UPDATE-only would silently no-op against an
empty table and the user's rule change would vanish.
"""

from __future__ import annotations


def _store(tmp_path):
    from subarr.migrate import run_migrations
    from subarr.schedule_store import ScheduleStore

    db = tmp_path / "sched.db"
    run_migrations(db)
    return ScheduleStore(db)


def test_get_rules_defaults_when_row_absent(tmp_path):
    from subarr.schedule_store import MODE_DASHBOARD

    rules = _store(tmp_path).get_rules()
    assert rules.mode == MODE_DASHBOARD
    assert rules.min_score == 200


def test_set_rules_persists_with_no_seed_row(tmp_path):
    """The auto_queue_rules row does not exist on a fresh migrated DB.
    set_rules must create it, not silently no-op."""
    from subarr.schedule_store import AutoQueueRules, MODE_MANUAL_CONFIRM

    store = _store(tmp_path)
    store.set_rules(AutoQueueRules(mode=MODE_MANUAL_CONFIRM, min_score=42))
    got = store.get_rules()
    assert got.mode == MODE_MANUAL_CONFIRM
    assert got.min_score == 42


def test_set_rules_updates_existing_row(tmp_path):
    from subarr.schedule_store import AutoQueueRules, MODE_AUTO_RULES, MODE_MANUAL_CONFIRM

    store = _store(tmp_path)
    store.set_rules(AutoQueueRules(mode=MODE_MANUAL_CONFIRM, min_score=42))
    store.set_rules(AutoQueueRules(mode=MODE_AUTO_RULES, min_score=99))
    got = store.get_rules()
    assert got.mode == MODE_AUTO_RULES
    assert got.min_score == 99
