"""Onboarding live-reload (audit #2).

The integration clients capture their URL / API-key at construction, and
the long-lived background loops (Scheduler, CompletionWatcher, ScanRunner,
coverage refresh) captured those clients at startup. So applying the
wizard's values to the frozen Settings object did nothing without a
restart.

These tests pin the fix:
  - the four runtime components resolve their client(s) through a
    provider lambda, so swapping app.state picks up live
  - _apply_progress_to_settings actually mutates the frozen Settings
  - _rebuild_runtime_clients swaps the clients on app.state and closes
    the old ones
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# conftest reloads subarr modules between tests — import locally so each
# test binds the current module's classes.
def _imports():
    from subarr.scheduler import Scheduler
    from subarr.completion_watcher import CompletionWatcher
    from subarr.scan_runner import ScanRunner
    return Scheduler, CompletionWatcher, ScanRunner


def test_scheduler_resolves_bundle_via_provider():
    Scheduler, _, _ = _imports()
    b1, b2 = object(), object()
    cur = {"b": b1}
    s = Scheduler(
        schedule_store=None,
        bundle_provider=lambda: cur["b"],
        scan_store=None,
        runner=None,
        provenance=None,
    )
    assert s._bundle is b1
    cur["b"] = b2
    assert s._bundle is b2  # picked up the swap, no restart


def test_completion_watcher_resolves_clients_via_providers():
    _, CompletionWatcher, _ = _imports()

    class Bundle:
        def __init__(self):
            self.bazarr = object()
            self.plex = object()

    b1, b2 = Bundle(), Bundle()
    sg1, sg2 = object(), object()
    cur = {"b": b1, "s": sg1}
    w = CompletionWatcher(
        bundle_provider=lambda: cur["b"],
        subgen_provider=lambda: cur["s"],
        provenance=None,
    )
    assert w._bazarr is b1.bazarr
    assert w._plex is b1.plex
    assert w._subgen is sg1
    cur["b"], cur["s"] = b2, sg2
    assert w._bazarr is b2.bazarr
    assert w._plex is b2.plex
    assert w._subgen is sg2


def test_scan_runner_resolves_subgen_via_provider():
    _, _, ScanRunner = _imports()
    sg1, sg2 = object(), object()
    cur = {"s": sg1}
    r = ScanRunner(store=MagicMock(), subgen_provider=lambda: cur["s"])
    assert r._subgen is sg1
    cur["s"] = sg2
    assert r._subgen is sg2


def test_existing_direct_construction_still_works():
    """Backward compat: passing a client directly (as the test suite and
    any not-yet-migrated caller does) must keep working."""
    _, CompletionWatcher, ScanRunner = _imports()
    sg = MagicMock()
    bazarr = MagicMock()
    plex = MagicMock()
    w = CompletionWatcher(subgen=sg, bazarr=bazarr, provenance=None, plex=plex)
    assert w._subgen is sg
    assert w._bazarr is bazarr
    assert w._plex is plex
    r = ScanRunner(subgen=sg, store=MagicMock())
    assert r._subgen is sg


def test_subgen_watchdog_resolves_subgen_via_provider():
    """The watchdog must probe the CURRENT subgen client. If it captured
    the boot client, _rebuild_runtime_clients closing the old client would
    pin caps to 'unreachable' forever."""
    from subarr.subgen_watchdog import SubgenWatchdog
    s1, s2 = object(), object()
    cur = {"s": s1}
    w = SubgenWatchdog(
        subgen_provider=lambda: cur["s"],
        get_caps=lambda: None,
        set_caps=lambda c: None,
    )
    assert w._subgen is s1
    cur["s"] = s2
    assert w._subgen is s2


def test_apply_progress_mutates_frozen_settings(monkeypatch):
    # Verifies the object.__setattr__ frozen-dataclass bypass. Must be a
    # field that is NOT env-set, or the clobber guard correctly skips it.
    monkeypatch.delenv("BAZARR_URL", raising=False)
    from subarr.routers.onboarding import _apply_progress_to_settings
    from subarr.config import settings

    _apply_progress_to_settings({"bazarr_url": "http://applied-in-test:6767"})
    assert settings.bazarr_url == "http://applied-in-test:6767"


@pytest.mark.asyncio
async def test_rebuild_runtime_clients_swaps_and_closes():
    from subarr.routers.onboarding import _rebuild_runtime_clients

    class FakeClient:
        def __init__(self):
            self.closed = False

        async def aclose(self):
            self.closed = True

    old_b, old_s, old_o = FakeClient(), FakeClient(), FakeClient()
    state = SimpleNamespace(
        integrations=old_b, subgen=old_s, ollama=old_o, subgen_caps="stale"
    )
    # reprobe=False keeps the test hermetic (no network to subgen).
    await _rebuild_runtime_clients(state, reprobe=False)

    assert state.integrations is not old_b
    assert state.subgen is not old_s
    assert state.ollama is not old_o
    assert old_b.closed and old_s.closed and old_o.closed
