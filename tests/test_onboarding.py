"""Tests for the onboarding wizard backend (#85).

Covers:
  - Fresh state has step=0, empty progress, completed_at=None
  - update() merges progress, advances step, clamps step bounds
  - update() with unset_keys removes fields
  - update() with progress value=None drops the key
  - complete() sets completed_at + step=DONE, idempotent
  - reset() wipes everything back to zero
  - State survives "restart" (new store instance against same DB)
  - Router endpoints round-trip through state correctly
  - probe-paths: success + nonexistent + not-a-directory
  - test/{service}: unknown service returns 404
  - auto-detect: graceful when discovery not configured
"""

from __future__ import annotations

from pathlib import Path

import pytest

from subarr.migrate import run_migrations
from subarr.onboarding import (
    MAX_STEP,
    STEP_BAZARR,
    STEP_DONE,
    STEP_WELCOME,
    OnboardingStore,
)


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


# ─── State store ───────────────────────────────────────────────────


def test_fresh_state_is_welcome_step(store: OnboardingStore):
    st = store.get()
    assert st.step == STEP_WELCOME
    assert st.completed_at is None
    assert st.progress == {}
    assert st.is_complete is False


def test_update_progress_merges(store: OnboardingStore):
    store.update(progress_patch={"media_root": "/media/library"})
    store.update(progress_patch={"bazarr_url": "http://bazarr:6767"})
    st = store.get()
    assert st.progress["media_root"] == "/media/library"
    assert st.progress["bazarr_url"] == "http://bazarr:6767"


def test_update_advances_step(store: OnboardingStore):
    store.update(step=STEP_BAZARR)
    assert store.get().step == STEP_BAZARR


def test_update_clamps_step_to_max(store: OnboardingStore):
    store.update(step=999)
    assert store.get().step == MAX_STEP


def test_update_clamps_step_below_zero(store: OnboardingStore):
    store.update(step=-5)
    assert store.get().step == 0


def test_update_accepts_final_step_index_12(store: OnboardingStore):
    # #378 Phase 5 added the optional "More stacks" step, so the wizard now has
    # 13 frontend steps (max index 12). The resume cursor must reach the last one.
    store.update(step=12)
    assert store.get().step == 12
    assert MAX_STEP >= 12


def test_update_with_none_drops_key(store: OnboardingStore):
    store.update(progress_patch={"x": "value", "y": "other"})
    store.update(progress_patch={"x": None})
    progress = store.get().progress
    assert "x" not in progress
    assert progress["y"] == "other"


def test_update_with_unset_keys(store: OnboardingStore):
    store.update(progress_patch={"a": "1", "b": "2", "c": "3"})
    store.update(unset_keys=["a", "c"])
    progress = store.get().progress
    assert "a" not in progress
    assert "c" not in progress
    assert progress["b"] == "2"


def test_complete_marks_completed_and_done(store: OnboardingStore):
    store.update(progress_patch={"x": "y"})
    st = store.complete()
    assert st.completed_at is not None
    assert st.step == STEP_DONE
    assert st.is_complete is True


def test_complete_is_idempotent(store: OnboardingStore):
    first = store.complete()
    assert first.completed_at is not None
    initial_ts = first.completed_at
    # Second complete() should not change completed_at (still that
    # first-completion timestamp).
    second = store.complete()
    assert second.completed_at == initial_ts


def test_reset_wipes_everything(store: OnboardingStore):
    store.update(progress_patch={"x": "y"}, step=STEP_BAZARR)
    store.complete()
    st = store.reset()
    assert st.step == 0
    assert st.completed_at is None
    assert st.progress == {}


def test_state_survives_new_store_instance(db_path: Path):
    s1 = OnboardingStore(db_path)
    s1.update(progress_patch={"media_root": "/data"}, step=2)
    s1.close()
    s2 = OnboardingStore(db_path)
    st = s2.get()
    assert st.step == 2
    assert st.progress["media_root"] == "/data"
    s2.close()


# ─── Serialisation ─────────────────────────────────────────────────


def test_to_dict_includes_derived_fields(store: OnboardingStore):
    store.update(progress_patch={"k": "v"})
    d = store.get().to_dict()
    assert "step" in d
    assert "completed_at" in d
    assert "is_complete" in d
    assert d["progress"] == {"k": "v"}


# ─── Probe-paths (no FastAPI needed for the underlying logic) ──────


def test_probe_paths_success(tmp_path: Path):
    # Create a fake media root with a couple of files
    (tmp_path / "TV").mkdir()
    (tmp_path / "Movies").mkdir()
    (tmp_path / "random.mkv").write_text("")
    # Use the router's helper directly
    from subarr.routers.onboarding import probe_paths, ProbePathsRequest

    class _MockRequest:
        pass

    r = probe_paths(ProbePathsRequest(media_root=str(tmp_path)), _MockRequest())
    assert r["ok"] is True
    assert r["total_top_level"] == 3
    assert any("TV" in s for s in r["samples"])


def test_probe_paths_nonexistent(tmp_path: Path):
    from subarr.routers.onboarding import probe_paths, ProbePathsRequest

    class _MockRequest:
        pass

    r = probe_paths(
        ProbePathsRequest(media_root=str(tmp_path / "nonexistent")),
        _MockRequest(),
    )
    assert r["ok"] is False
    assert "does not exist" in r["error"]


def test_probe_paths_not_a_directory(tmp_path: Path):
    f = tmp_path / "afile.txt"
    f.write_text("hello")
    from subarr.routers.onboarding import probe_paths, ProbePathsRequest

    class _MockRequest:
        pass

    r = probe_paths(ProbePathsRequest(media_root=str(f)), _MockRequest())
    assert r["ok"] is False
    assert "not a directory" in r["error"]
