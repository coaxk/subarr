"""#202 activation slice 1: auto-kick the first walk on setup completion.

Guards (in _maybe_auto_first_walk): an arr must be configured (coverage source)
and no scan may have run yet (don't duplicate a manual wizard walk). Always
best-effort — never raise into /complete.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from subarr.routers.onboarding import _maybe_auto_first_walk


class _Walker:
    def __init__(self):
        self.calls: list[str] = []

    async def start_walk(self, root):
        self.calls.append(root)
        return SimpleNamespace(id=len(self.calls), root=root)


def _arr(configured: bool):
    return SimpleNamespace(is_configured=lambda: configured)


def _app_state(*, arrs=True, scans=0, roots=None):
    return SimpleNamespace(
        probe_walker=_Walker(),
        scans=SimpleNamespace(count_since=lambda _since: scans),
        integrations=SimpleNamespace(bazarr=_arr(arrs), sonarr=_arr(False), radarr=_arr(False)),
        onboarding=SimpleNamespace(get=lambda: SimpleNamespace(progress={"probe_roots": roots or ["TV"]})),
        schedule=SimpleNamespace(update_schedule=lambda *a, **k: None),
    )


@pytest.mark.asyncio
async def test_kicks_when_arr_configured_and_no_scan():
    st = _app_state(arrs=True, scans=0)
    await _maybe_auto_first_walk(st)
    assert st.probe_walker.calls == ["TV"]


@pytest.mark.asyncio
async def test_skips_when_no_arr_configured():
    st = _app_state(arrs=False, scans=0)
    await _maybe_auto_first_walk(st)
    assert st.probe_walker.calls == []


@pytest.mark.asyncio
async def test_skips_when_a_scan_already_exists():
    st = _app_state(arrs=True, scans=5)
    await _maybe_auto_first_walk(st)
    assert st.probe_walker.calls == []


@pytest.mark.asyncio
async def test_best_effort_never_raises():
    st = _app_state(arrs=True, scans=0)

    def _boom(_since):
        raise RuntimeError("scan store down")

    st.scans = SimpleNamespace(count_since=_boom)
    # must not propagate — completion can't be broken by the walk kick
    await _maybe_auto_first_walk(st)
    assert st.probe_walker.calls == []


# ─── route wiring: /complete auto-kicks once ────────────────────────


def test_complete_auto_kicks_walk_once(app_with_stub):
    # Fresh install: onboarding incomplete, no scans, arrs configured (stub
    # default) → first /complete kicks a walk; a second /complete does not.
    calls: list[str] = []

    async def _rec(root):
        calls.append(root)
        return SimpleNamespace(id=len(calls), root=root)

    app_with_stub.app.state.probe_walker.start_walk = _rec

    assert app_with_stub.post("/api/onboarding/complete").status_code == 200
    assert len(calls) >= 1
    first = len(calls)
    # already complete → no duplicate walk
    assert app_with_stub.post("/api/onboarding/complete").status_code == 200
    assert len(calls) == first
