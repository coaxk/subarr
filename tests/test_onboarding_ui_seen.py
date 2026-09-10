"""#480: 87% of incomplete installs sit at the Welcome step, which is
ambiguous between "opened the UI, saw the first screen, bounced" and "never
opened the UI at all". Those call for opposite responses (a first-screen
problem vs a discovery problem) and the telemetry could not separate them.

One flag settles it: whether the onboarding page was ever RENDERED. The wizard
posts it on mount, the store keeps the first time it happened, and telemetry
reports the boolean. It survives a "re-run setup" reset, because the question
is "was it ever seen", not "has it been seen since the last reset"."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from subarr.migrate import run_migrations
from subarr.onboarding import OnboardingStore


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "subarr.db"
    run_migrations(p)
    return p


@pytest.fixture
def store(db_path: Path) -> OnboardingStore:
    s = OnboardingStore(db_path)
    yield s
    s.close()


def test_fresh_store_has_never_seen_the_ui(store):
    st = store.get()
    assert st.ui_seen_at is None
    assert st.to_dict()["ui_seen"] is False


def test_mark_ui_seen_records_the_first_time_and_is_idempotent(store):
    before = time.time()
    first = store.mark_ui_seen()
    assert first.ui_seen_at is not None
    assert first.ui_seen_at >= before - 1
    time.sleep(0.01)
    second = store.mark_ui_seen()
    assert second.ui_seen_at == first.ui_seen_at, "a second render must not move the FIRST-seen time"
    assert second.to_dict()["ui_seen"] is True


def test_ui_seen_survives_reset_and_restart(db_path, store):
    store.mark_ui_seen()
    store.reset()
    assert store.get().ui_seen_at is not None, "re-run setup must not forget the UI was ever rendered"
    again = OnboardingStore(db_path)
    try:
        assert again.get().ui_seen_at is not None
    finally:
        again.close()


def test_router_seen_endpoint_marks_and_returns_state(store):
    from subarr.routers.onboarding import mark_seen

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(onboarding=store)))
    body = mark_seen(request)
    assert body["ui_seen"] is True
    assert store.get().ui_seen_at is not None


def test_stats_provider_reports_the_flag(store):
    from subarr.telemetry import _onboarding_ui_seen

    app_state = SimpleNamespace(onboarding=store)
    assert _onboarding_ui_seen(app_state) is False
    store.mark_ui_seen()
    assert _onboarding_ui_seen(app_state) is True
    # No store at all: unknown, never a confident False.
    assert _onboarding_ui_seen(SimpleNamespace()) is None


def test_payload_carries_onboarding_ui_seen(db_path):
    from tests.test_telemetry import _FakeCaps, _make_collector

    stats = {"onboarding_ui_seen": True}
    c = _make_collector(db_path, stats=stats, caps=_FakeCaps())
    d = c.build_payload().to_dict()
    assert d["onboarding_ui_seen"] is True
    c2 = _make_collector(db_path, stats={}, caps=_FakeCaps())
    assert c2.build_payload().to_dict()["onboarding_ui_seen"] is None
