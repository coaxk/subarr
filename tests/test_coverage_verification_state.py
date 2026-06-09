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

    return CoverageItem(media_type="episode", title="Show", canonical_path=canonical, episode_number=epnum)


def _probe(path):
    from subarr.media_probe import ProbeResult

    return ProbeResult(canonical_path=path)


def test_disqualify_unsupported_iso_and_disc_images():
    # #96/#62: a multi-episode .iso (resolved file) is disqualified to
    # "unsupported" so it leaves "Analyzing"; real video files and
    # folder-only rows are untouched, and already-verified rows are left alone.
    from subarr.coverage_engine import CoverageItem, _disqualify_unsupported

    iso = CoverageItem(
        media_type="episode",
        title="Klovn",
        canonical_path="TV/Klovn",
        episode_number="1x3",
        file_canonical_path="TV/Klovn/Klovn Season 1.iso",
    )
    mkv = CoverageItem(
        media_type="episode",
        title="Show",
        canonical_path="TV/Show",
        episode_number="1x1",
        file_canonical_path="TV/Show/S01E01.mkv",
    )
    folder = CoverageItem(
        media_type="episode", title="Folder", canonical_path="TV/Folder", episode_number="1x1"
    )  # no resolved file → folder-only
    verified_iso = CoverageItem(
        media_type="episode",
        title="V",
        canonical_path="TV/V",
        episode_number="1x1",
        file_canonical_path="TV/V/x.iso",
    )
    verified_iso.verification_state = "verified"

    _disqualify_unsupported([iso, mkv, folder, verified_iso])

    assert iso.verification_state == "unsupported"  # disc image → disqualified
    assert mkv.verification_state == "unprobed"  # real video → untouched
    assert folder.verification_state == "unprobed"  # resolution gap → separate concern
    assert verified_iso.verification_state == "verified"  # never downgrade a verified row


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
    _attach_probe_episode(item, {}, failed_idx={"TV/Show": ["TV/Show/Season 1/Show - S01E04 - y.mkv"]})
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


# ── folder-row fix: propagate Sonarr episodeFile path onto the row ──────


def test_episode_file_canonical_resolves_from_sonarr(subarr_env):
    from subarr.coverage_engine import _episode_file_canonical

    eps = {9001: {"id": 9001, "episodeFileId": 555}}
    paths = {555: "/data/Media/TV/Klovn/Season 1/Klovn.S01E01.mkv"}  # ARR_PATH_PREFIX=/data/Media/
    assert _episode_file_canonical(9001, paths, eps) == "TV/Klovn/Season 1/Klovn.S01E01.mkv"


def test_episode_file_canonical_none_when_fileless(subarr_env):
    from subarr.coverage_engine import _episode_file_canonical

    assert _episode_file_canonical(9001, {}, {9001: {"id": 9001}}) is None  # no episodeFileId
    assert (
        _episode_file_canonical(9001, {}, {9001: {"id": 9001, "episodeFileId": 5}}) is None
    )  # id but no path
    assert _episode_file_canonical(None, {}, {}) is None
