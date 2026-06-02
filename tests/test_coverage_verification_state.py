"""Probe-gate tagging: every coverage row carries verification_state.

  verified     — a probe matched this row (audio/embedded data resolved)
  probe_failed — the file is recorded in probe_failures (couldn't analyze)
  unprobed     — no probe and no failure yet (not analyzed)

Only `verified` rows are confident gaps; the others are bucketed and held
until the probe runs (see the sticky-bucket PR).
"""

from __future__ import annotations


def _ep(epnum, canonical="TV/Show"):
    from subarr.coverage_engine import CoverageItem
    return CoverageItem(media_type="episode", title="Show",
                        canonical_path=canonical, episode_number=epnum)


def _probe(path):
    from subarr.media_probe import ProbeResult
    return ProbeResult(canonical_path=path)


def test_episode_verified_on_probe_match():
    from subarr.coverage_engine import _attach_probe_episode
    item = _ep("1x3")
    f = "TV/Show/Season 1/Show - S01E03 - x.mkv"
    _attach_probe_episode(item, {"TV/Show": [(f, _probe(f))]}, failed_idx={})
    assert item.verification_state == "verified"
    assert item.file_canonical_path == f


def test_episode_probe_failed_when_only_failure_matches():
    from subarr.coverage_engine import _attach_probe_episode
    item = _ep("1x4")
    _attach_probe_episode(
        item, {}, failed_idx={"TV/Show": ["TV/Show/Season 1/Show - S01E04 - y.mkv"]}
    )
    assert item.verification_state == "probe_failed"


def test_episode_unprobed_when_no_match():
    from subarr.coverage_engine import _attach_probe_episode
    item = _ep("1x5")
    _attach_probe_episode(item, {}, failed_idx={})
    assert item.verification_state == "unprobed"


def test_movie_states():
    from subarr.coverage_engine import _attach_probe_movie, CoverageItem
    mv = CoverageItem(media_type="movie", title="M", canonical_path="Movies/M (2024)")
    f = "Movies/M (2024)/M.mkv"
    _attach_probe_movie(mv, {"Movies/M (2024)": [(f, _probe(f))]}, failed_idx={})
    assert mv.verification_state == "verified"

    mf = CoverageItem(media_type="movie", title="M2", canonical_path="Movies/M2")
    _attach_probe_movie(mf, {}, failed_idx={"Movies/M2": ["Movies/M2/M2.mkv"]})
    assert mf.verification_state == "probe_failed"

    mu = CoverageItem(media_type="movie", title="M3", canonical_path="Movies/M3")
    _attach_probe_movie(mu, {}, failed_idx={})
    assert mu.verification_state == "unprobed"


def test_verification_state_in_to_dict():
    from subarr.coverage_engine import CoverageItem
    d = CoverageItem(media_type="episode", title="X").to_dict()
    assert d["verification_state"] == "unprobed"
