"""#157 Phase 1 — TaskHealthStore: per-loop health so silent crashes surface."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

import subarr
from subarr.task_health import TaskHealthStore, TaskHealth

_SQL = (Path(subarr.__file__).parent / "migrations" / "012_task_health.sql").read_text()


@pytest.fixture
def store(tmp_path):
    s = TaskHealthStore(tmp_path / "th.db")
    s._conn.executescript(_SQL)
    return s


def test_record_success_then_failure(store):
    store.record_success("coverage-cache", expected_interval_s=300)
    st = {t.task_name: t for t in store.states()}["coverage-cache"]
    assert st.consecutive_failures == 0 and st.total_runs == 1
    assert st.last_success_at is not None and st.is_unhealthy is False

    try:
        raise ValueError("boom")
    except ValueError as e:
        store.record_failure("coverage-cache", e)
    st = {t.task_name: t for t in store.states()}["coverage-cache"]
    assert st.consecutive_failures == 1
    assert st.last_error_type == "ValueError"
    assert "boom" in (st.last_error_detail or "")  # traceback captured
    assert "ValueError" in (st.last_error_detail or "")
    assert st.total_runs == 2 and st.total_failures == 1


def test_three_consecutive_failures_unhealthy_then_recovers(store):
    for _ in range(3):
        try:
            raise RuntimeError("x")
        except RuntimeError as e:
            store.record_failure("scheduler", e)
    st = {t.task_name: t for t in store.states()}["scheduler"]
    assert st.consecutive_failures == 3 and st.is_unhealthy is True

    store.record_success("scheduler")
    st = {t.task_name: t for t in store.states()}["scheduler"]
    assert st.consecutive_failures == 0 and st.is_unhealthy is False


def test_staleness_marks_unhealthy_even_without_recent_failures():
    now = time.time()
    fresh = TaskHealth(
        task_name="t",
        last_success_at=now - 5,
        last_error_at=None,
        last_error_type=None,
        last_error_detail=None,
        consecutive_failures=0,
        total_runs=10,
        total_failures=0,
        expected_interval_s=10,
        updated_at=now,
    )
    stale = TaskHealth(
        task_name="t",
        last_success_at=now - 100,
        last_error_at=None,
        last_error_type=None,
        last_error_detail=None,
        consecutive_failures=0,
        total_runs=10,
        total_failures=0,
        expected_interval_s=10,
        updated_at=now,
    )  # 100 > 3*10
    assert fresh.is_unhealthy is False
    assert stale.is_unhealthy is True


def test_never_run_is_not_unhealthy():
    h = TaskHealth(
        task_name="t",
        last_success_at=None,
        last_error_at=None,
        last_error_type=None,
        last_error_detail=None,
        consecutive_failures=0,
        total_runs=0,
        total_failures=0,
        expected_interval_s=300,
        updated_at=time.time(),
    )
    assert h.is_unhealthy is False


def _health(**over):
    base = dict(
        task_name="t",
        last_success_at=1000.0,
        last_error_at=None,
        last_error_type=None,
        last_error_detail=None,
        consecutive_failures=0,
        total_runs=1,
        total_failures=0,
        expected_interval_s=60.0,
        updated_at=1000.0,
    )
    base.update(over)
    return TaskHealth(**base)


def test_next_run_at_is_last_success_plus_interval():
    assert _health(last_success_at=1000.0, expected_interval_s=60.0).next_run_at == 1060.0


def test_next_run_at_none_without_interval():
    # event-driven loop (no cadence) has no schedule to project
    assert _health(expected_interval_s=None).next_run_at is None


def test_next_run_at_none_before_first_success():
    assert _health(last_success_at=None).next_run_at is None


def test_to_dict_exposes_next_run_at():
    d = _health(last_success_at=1000.0, expected_interval_s=60.0).to_dict()
    assert d["next_run_at"] == 1060.0


def test_record_is_best_effort_never_raises(store):
    store.close()  # break the connection
    # Must not raise even though the DB is unusable — health recording must
    # never crash the loop it monitors.
    store.record_success("x")
    store.record_failure("x", ValueError("y"))
