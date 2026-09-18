"""#561: an episode whose file Sonarr has already resolved is matched to its
probe by that exact path, not by an SxxExx token in the filename.

Date-named episodes (`Daily Show - 2026-09-08.mkv`) never contain the token, so
a valid cached probe was never attached and the row sat in Analyzing, while
eager-probe kept skipping the file as already cached. The same token-only match
could also attach a probe of a DIFFERENT file (an old release still in the
cache) to a row that knows its current file.
"""

from __future__ import annotations

SHOW = "TV/Daily Show"
DATED = "TV/Daily Show/Season 31/Daily Show - 2026-09-08.mkv"


def _probe(path):
    from subarr.media_probe import ProbeResult

    return ProbeResult(canonical_path=path)


def _index(*paths):
    """Same prefix scheme as build_coverage: every ancestor folder."""
    idx: dict[str, list] = {}
    for p in paths:
        parts = p.split("/")
        for i in range(2, len(parts)):
            idx.setdefault("/".join(parts[:i]), []).append((p, _probe(p)))
    return idx


def _failed(*paths):
    idx: dict[str, list] = {}
    for p in paths:
        parts = p.split("/")
        for i in range(2, len(parts)):
            idx.setdefault("/".join(parts[:i]), []).append(p)
    return idx


def _item(file_path=None, episode_number="31x97", canonical=SHOW):
    from subarr.coverage_engine import CoverageItem

    return CoverageItem(
        media_type="episode",
        title="Daily Show",
        canonical_path=canonical,
        episode_number=episode_number,
        file_canonical_path=file_path,
    )


def _attach(item, idx, failed=None):
    from subarr.coverage_engine import _attach_probe_episode

    _attach_probe_episode(item, idx, failed_idx=failed or {})


def test_date_named_episode_is_verified_by_its_exact_path():
    item = _item(DATED)
    _attach(item, _index(DATED))
    assert item.verification_state == "verified"
    assert item.file_canonical_path == DATED


def test_a_probe_of_a_different_file_is_not_attached():
    """An old release still in the cache carries the right SxxExx token, but the
    row knows its current file: the old probe must not verify it."""
    old = "TV/Show/Season 1/Show - S01E03 - old release.mp4"
    new = "TV/Show/Season 1/Show - S01E03 - new release.mkv"
    item = _item(new, episode_number="1x3", canonical="TV/Show")
    _attach(item, _index(old))
    assert item.verification_state == "unprobed"
    assert item.file_canonical_path == new


def test_exact_match_wins_over_a_token_match_on_another_file():
    old = "TV/Show/Season 1/Show - S01E03 - old release.mp4"
    new = "TV/Show/Season 1/Show - S01E03 - new release.mkv"
    item = _item(new, episode_number="1x3", canonical="TV/Show")
    _attach(item, _index(old, new))
    assert item.verification_state == "verified"
    assert item.file_canonical_path == new


def test_recorded_failure_for_the_exact_file_is_probe_failed():
    item = _item(DATED)
    _attach(item, {}, failed=_failed(DATED))
    assert item.verification_state == "probe_failed"


def test_a_failure_of_a_different_file_does_not_mark_this_one_failed():
    old = "TV/Show/Season 1/Show - S01E03 - old release.mp4"
    new = "TV/Show/Season 1/Show - S01E03 - new release.mkv"
    item = _item(new, episode_number="1x3", canonical="TV/Show")
    _attach(item, {}, failed=_failed(old))
    assert item.verification_state == "unprobed"


def test_no_episode_number_still_matches_by_exact_path():
    item = _item(DATED, episode_number=None)
    _attach(item, _index(DATED))
    assert item.verification_state == "verified"


def test_exact_path_is_found_even_if_the_row_prefix_differs():
    """The lookup keys on the file's own folder, not on the row's series prefix."""
    item = _item(DATED, canonical="TV/Daily Show (US)")
    _attach(item, _index(DATED))
    assert item.verification_state == "verified"


def test_legacy_token_fallback_without_a_resolved_file():
    f = "TV/Show/Season 1/Show - S01E03 - x.mkv"
    item = _item(None, episode_number="1x3", canonical="TV/Show")
    _attach(item, _index(f))
    assert item.verification_state == "verified"
    assert item.file_canonical_path == f


def test_legacy_token_failure_without_a_resolved_file():
    f = "TV/Show/Season 1/Show - S01E03 - x.mkv"
    item = _item(None, episode_number="1x3", canonical="TV/Show")
    _attach(item, {}, failed=_failed(f))
    assert item.verification_state == "probe_failed"


def test_date_named_row_is_not_re_queued_for_eager_probe_once_attached():
    """The loop the reporter saw: refresh queues the file, probe skips it as
    cached, the row stays unprobed. After the fix the row is verified, so
    eager-probe no longer selects it."""
    from dataclasses import asdict

    from subarr.coverage_cache import eager_probe_targets

    item = _item(DATED)
    _attach(item, _index(DATED))
    assert eager_probe_targets([asdict(item)]) == []
