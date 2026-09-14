"""#546: probe roots that do not exist failed every scheduled walk silently.

#524 (Jorman): the wizard pre-filled `TV, Movies`; his library holds `Film` and
`Serie Tv`. Every scheduled walk ended `root not found` and nothing said so:
the Rules page read "2 roots -- ffprobe runs" and the embedded-subtitle cache
behind "Skip embedded EN" was never filled by those walks.

Three guards:
1. the API says which saved roots do not resolve, and refuses to save one;
2. the scheduler logs a warning when a walk's root is missing;
3. the wizard suggests roots that exist, and never persists one that does not.

Folder names are unique per test: the media root can be shared across tests,
so assertions check properties ("every suggested root exists") rather than
exact lists. Imports are local: conftest reloads subarr modules.
"""

from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace

import pytest


def _mkdirs(*names):
    from subarr.config import settings

    for n in names:
        (settings.media_root / n).mkdir(parents=True, exist_ok=True)


def _uniq(label: str) -> str:
    return f"{label} {uuid.uuid4().hex[:6]}"


# ─── 1. the API ─────────────────────────────────────────────────────────────


def test_schedule_reports_which_probe_roots_do_not_resolve(app_with_stub):
    film, missing = _uniq("Film"), _uniq("TV")
    _mkdirs(film)
    app_with_stub.app.state.schedule.update_schedule("coverage_walk", probe_roots=f"{film},{missing}")

    sched = next(
        s for s in app_with_stub.get("/api/schedule").json()["schedules"] if s["name"] == "coverage_walk"
    )
    check = {c["root"]: c for c in sched["probe_roots_check"]}
    assert check[film]["ok"] is True
    assert check[missing]["ok"] is False
    assert "not found" in check[missing]["reason"]


def test_saving_a_probe_root_that_does_not_exist_is_refused_and_named(app_with_stub):
    film, missing = _uniq("Film"), _uniq("Movies")
    _mkdirs(film)
    r = app_with_stub.patch("/api/schedule/coverage_walk", json={"probe_roots": f"{film}, {missing}"})
    assert r.status_code == 422, r.text
    assert missing in r.text
    # nothing was written
    sched = next(
        s for s in app_with_stub.get("/api/schedule").json()["schedules"] if s["name"] == "coverage_walk"
    )
    assert missing not in sched["probe_roots"]


def test_saving_probe_roots_that_exist_persists(app_with_stub):
    a, b = _uniq("Film"), _uniq("Serie Tv")
    _mkdirs(a, b)
    r = app_with_stub.patch("/api/schedule/coverage_walk", json={"probe_roots": f"{a}, {b}"})
    assert r.status_code == 200, r.text
    assert r.json()["probe_roots"] == [a, b]


def test_clearing_probe_roots_is_still_allowed(app_with_stub):
    r = app_with_stub.patch("/api/schedule/coverage_walk", json={"probe_roots": ""})
    assert r.status_code == 200, r.text
    assert r.json()["probe_roots"] == []


def test_an_unknown_library_head_is_refused(app_with_stub):
    r = app_with_stub.patch("/api/schedule/coverage_walk", json={"probe_roots": "@no-such-library-546/"})
    assert r.status_code == 422, r.text
    assert "@no-such-library-546/" in r.text


# ─── 2. the scheduler ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_walk_whose_root_is_missing_logs_a_warning(caplog):
    import subarr.scheduler as sched_mod

    class _Walker:
        async def start_walk(self, root):
            return SimpleNamespace(
                status="error",
                total_files=0,
                probed=0,
                cached_hits=0,
                errors=[{"error": f"root not found: {root}"}],
            )

    s = sched_mod.Scheduler.__new__(sched_mod.Scheduler)
    s._probe_walker = _Walker()
    with caplog.at_level(logging.WARNING, logger="subarr.scheduler"):
        summary = await s._run_probe_walks(["TV"])

    assert summary["walks"][0]["status"] == "error"
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("TV" in m and "root not found" in m for m in warnings), warnings


# ─── 3. the wizard ───────────────────────────────────────────────────────────


class _Walker:
    def __init__(self):
        self.calls: list[str] = []

    async def start_walk(self, root):
        self.calls.append(root)
        return SimpleNamespace(id=len(self.calls), root=root)


def _state(progress_roots):
    persisted = {}

    def update_schedule(name, **kw):
        persisted[name] = kw

    return (
        SimpleNamespace(
            probe_walker=_Walker(),
            onboarding=SimpleNamespace(get=lambda: SimpleNamespace(progress={"probe_roots": progress_roots})),
            schedule=SimpleNamespace(update_schedule=update_schedule),
        ),
        persisted,
    )


@pytest.mark.asyncio
async def test_first_walk_with_no_roots_chosen_suggests_only_folders_that_exist():
    from subarr.config import settings
    from subarr.routers.onboarding import _kick_first_walk

    film, serie = _uniq("Film"), _uniq("Serie Tv")
    _mkdirs(film, serie)
    (settings.media_root / f"{_uniq('loose')}.mkv").write_bytes(b"")

    st, persisted = _state(None)
    result = await _kick_first_walk(st)

    roots = result["schedule_probe_roots"]
    assert film in roots and serie in roots
    assert all((settings.media_root / r).is_dir() for r in roots), roots
    assert st.probe_walker.calls == roots
    assert persisted["coverage_walk"]["probe_roots"] == ",".join(roots)


@pytest.mark.asyncio
async def test_first_walk_skips_and_reports_a_chosen_root_that_does_not_exist():
    from subarr.routers.onboarding import _kick_first_walk

    film, missing = _uniq("Film"), _uniq("TV")
    _mkdirs(film)
    st, persisted = _state([film, missing])
    result = await _kick_first_walk(st)

    assert result["schedule_probe_roots"] == [film]
    assert result["skipped_roots"] == [{"root": missing, "reason": f"root not found: {missing}"}]
    assert st.probe_walker.calls == [film]
    assert persisted["coverage_walk"]["probe_roots"] == film


def test_the_wizard_can_ask_which_roots_exist(app_with_stub):
    from subarr.config import settings

    film = _uniq("Film")
    _mkdirs(film)
    loose = f"{_uniq('loose')}.mkv"
    (settings.media_root / loose).write_bytes(b"")

    r = app_with_stub.get("/api/onboarding/probe-root-suggestions")
    assert r.status_code == 200, r.text
    roots = r.json()["roots"]
    assert film in roots
    assert loose not in roots
    assert all((settings.media_root / x).is_dir() for x in roots)
