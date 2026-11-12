"""#666: unit cases for `paused_states`."""

from __future__ import annotations

import logging
import types

from subarr.job_pause import paused_states


class FakeStore:
    def __init__(
        self,
        *,
        queue_paused=False,
        enabled=True,
        row_present=True,
        rules_error=None,
        schedule_error=None,
        rules_sequence=None,
    ):
        self.queue_paused = queue_paused
        self.enabled = enabled
        self.row_present = row_present
        self.rules_error = rules_error
        self.schedule_error = schedule_error
        self.rules_sequence = list(rules_sequence) if rules_sequence is not None else None
        self.requested_schedules = []

    def get_rules(self):
        if self.rules_sequence is not None:
            action = self.rules_sequence.pop(0)
            if isinstance(action, Exception):
                raise action
            return types.SimpleNamespace(queue_paused=action)
        if self.rules_error is not None:
            raise self.rules_error
        return types.SimpleNamespace(queue_paused=self.queue_paused)

    def get_schedule(self, name):
        self.requested_schedules.append(name)
        if self.schedule_error is not None:
            raise self.schedule_error
        if not self.row_present:
            return None
        return types.SimpleNamespace(enabled=self.enabled)


def _debug_records(caplog):
    return [r for r in caplog.records if r.name == "subarr.job_pause" and r.levelno == logging.DEBUG]


def test_none_schedule_reports_nothing():
    assert paused_states(None) == {}


def test_unpaused_store_reports_nothing():
    assert paused_states(FakeStore(queue_paused=False, enabled=True)) == {}


def test_feed_paused_reports_only_the_feed():
    assert paused_states(FakeStore(queue_paused=True, enabled=True)) == {
        "queue-feeder": {"label": "feed paused", "href": "/queue"}
    }


def test_walk_off_reports_only_the_walk():
    assert paused_states(FakeStore(queue_paused=False, enabled=False)) == {
        "scheduler": {"label": "coverage walk off", "href": "/rules"}
    }


def test_both_paused_reports_both_and_nothing_else():
    assert paused_states(FakeStore(queue_paused=True, enabled=False)) == {
        "queue-feeder": {"label": "feed paused", "href": "/queue"},
        "scheduler": {"label": "coverage walk off", "href": "/rules"},
    }


def test_missing_schedule_row_adds_no_walk_entry():
    assert paused_states(FakeStore(queue_paused=True, row_present=False)) == {
        "queue-feeder": {"label": "feed paused", "href": "/queue"}
    }


def test_schedule_is_read_by_the_coverage_walk_name():
    store = FakeStore()
    paused_states(store)
    assert store.requested_schedules == ["coverage_walk"]


def test_rules_failure_logs_the_type_without_the_message(caplog):
    caplog.set_level(logging.DEBUG, logger="subarr.job_pause")
    store = FakeStore(enabled=False, rules_error=RuntimeError("secret-detail-xyz"))

    assert paused_states(store) == {"scheduler": {"label": "coverage walk off", "href": "/rules"}}

    records = _debug_records(caplog)
    assert len(records) == 1
    assert "RuntimeError" in records[0].getMessage()
    assert "secret-detail-xyz" not in records[0].getMessage()


def test_schedule_failure_logs_the_type_without_the_message(caplog):
    caplog.set_level(logging.DEBUG, logger="subarr.job_pause")
    store = FakeStore(queue_paused=True, schedule_error=ValueError("secret-detail-xyz"))

    assert paused_states(store) == {"queue-feeder": {"label": "feed paused", "href": "/queue"}}

    records = _debug_records(caplog)
    assert len(records) == 1
    assert "ValueError" in records[0].getMessage()
    assert "secret-detail-xyz" not in records[0].getMessage()


def test_both_reads_failing_reports_nothing_and_logs_twice(caplog):
    caplog.set_level(logging.DEBUG, logger="subarr.job_pause")
    store = FakeStore(
        rules_error=RuntimeError("rules-broke"),
        schedule_error=ValueError("schedule-broke"),
    )

    assert paused_states(store) == {}

    assert len(_debug_records(caplog)) == 2


def test_rules_recovers_after_a_failure():
    store = FakeStore(enabled=True, rules_sequence=[RuntimeError("first"), True])

    assert "queue-feeder" not in paused_states(store)
    assert paused_states(store)["queue-feeder"] == {"label": "feed paused", "href": "/queue"}


def test_result_is_a_fresh_dict_each_call():
    store = FakeStore(queue_paused=True, enabled=False)

    first = paused_states(store)
    first["queue-feeder"]["label"] = "mutated"
    first["scheduler"]["href"] = "/mutated"
    first["extra"] = {"label": "extra", "href": "/extra"}

    assert paused_states(store) == {
        "queue-feeder": {"label": "feed paused", "href": "/queue"},
        "scheduler": {"label": "coverage walk off", "href": "/rules"},
    }


def test_truthiness_not_identity_marks_a_pause():
    assert paused_states(FakeStore(queue_paused=1, enabled=0)) == {
        "queue-feeder": {"label": "feed paused", "href": "/queue"},
        "scheduler": {"label": "coverage walk off", "href": "/rules"},
    }
