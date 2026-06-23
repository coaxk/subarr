"""#134 Phase 1 slices 3-4: walkers + direct media_root joins are library-aware."""

from __future__ import annotations

import asyncio


def test_probe_walker_walks_non_default_library(two_libraries):
    """start_walk('@disk2/Movies') must enumerate files under disk2's root,
    not media_root, and record canonicals with the @disk2/ head."""
    from subarr.config import settings
    from subarr.migrate import run_migrations
    from subarr.probe_store import ProbeStore
    from subarr.probe_walker import ProbeWalker

    run_migrations(settings.db_path)
    store = ProbeStore(settings.db_path)
    walker = ProbeWalker(store)

    async def run():
        state = await walker.start_walk("@disk2/Movies")
        while state.status == "running":
            await asyncio.sleep(0.05)
        return state

    state = asyncio.run(run())
    # The walk must FIND the file (enumeration worked under disk2's root).
    # ffprobe on the empty stub file fails -> recorded as an error with the
    # @disk2/ canonical, proving fs_to_canonical emitted the library head.
    assert state.total_files == 1
    assert state.status == "done"
    paths_seen = [e.get("path", "") for e in state.errors]
    assert any(p.startswith("@disk2/Movies/") for p in paths_seen)


def test_probe_walker_unknown_library_errors_cleanly(two_libraries):
    from subarr.config import settings
    from subarr.probe_store import ProbeStore
    from subarr.probe_walker import ProbeWalker

    walker = ProbeWalker(ProbeStore(settings.db_path))

    async def run():
        state = await walker.start_walk("@nope/x")
        while state.status == "running":
            await asyncio.sleep(0.05)
        return state

    state = asyncio.run(run())
    assert state.status == "error"


def test_srt_scan_resolves_in_non_default_library(two_libraries):
    from subarr import coverage_engine

    movies = two_libraries / "Movies"
    (movies / "film.en.srt").write_text("1\n")
    has, srts = coverage_engine._scan_for_srt("@disk2/Movies")
    assert has is True and srts == ["film.en.srt"]
    # recursive variant too
    rel = coverage_engine._scan_for_srt_recursive("@disk2/Movies")
    assert rel == ["film.en.srt"]
    # unknown library -> benign empty (PathOutsideRootError is a ValueError)
    assert coverage_engine._scan_for_srt("@nope/x") == (False, [])
    assert coverage_engine._scan_for_srt_recursive("@nope/x") == []


def test_audio_audit_walk_spans_all_libraries(two_libraries):
    """The audio-audit deep-scan worklist must enumerate video files across
    EVERY library root, emitting @slug/ canonicals for non-default ones."""
    import importlib

    from subarr import app as app_mod

    importlib.reload(app_mod)
    canons = [c for c, _mt in app_mod._walk_all_library_files()]
    assert any(c.startswith("@disk2/") for c in canons), canons
    assert any(not c.startswith("@") for c in canons), canons  # library 0 too


def test_sidecar_root_guard_accepts_all_library_roots(two_libraries):
    import importlib

    from subarr.routers import sidecar

    importlib.reload(sidecar)
    p = two_libraries / "Movies" / "film.mkv"
    resolved = sidecar._resolve_under_root(str(p))  # must NOT raise
    assert resolved == p.resolve()


def test_sidecar_root_guard_still_rejects_outside(two_libraries, tmp_path):
    import importlib

    import pytest
    from fastapi import HTTPException

    from subarr.routers import sidecar

    importlib.reload(sidecar)
    with pytest.raises(HTTPException):
        sidecar._resolve_under_root(str(tmp_path / "outside" / "x.srt"))


def test_library_canonical_resolves_for_partial_scan(two_libraries):
    """The /api/plex/partial-scan canonical branch (and the completion
    watcher's sidecar lookups) now resolve via canonical_to_fs — a @disk2/
    canonical must land under disk2's root, not media_root."""
    from subarr.paths import canonical_to_fs

    expected = (two_libraries / "Movies" / "film.mkv").resolve()
    assert canonical_to_fs("@disk2/Movies/film.mkv") == expected


def test_partial_scan_absolute_path_containment(two_libraries):
    """#285: an absolute /api/plex/partial-scan path that resolves outside every
    configured library root is rejected (400), not forwarded to Plex's scan API.
    A path under a library root passes."""
    import pytest
    from fastapi import HTTPException

    from subarr.paths import canonical_to_fs
    from subarr.routers.admin import _ensure_abs_path_contained

    inside = canonical_to_fs("@disk2/Movies/film.mkv")  # under disk2's root
    _ensure_abs_path_contained(inside)  # contained → must not raise

    with pytest.raises(HTTPException) as ei:
        _ensure_abs_path_contained("/nonexistent-root/etc/passwd")
    assert ei.value.status_code == 400
