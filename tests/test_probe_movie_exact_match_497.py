"""#497: a movie row must not inherit ffprobe data from a DIFFERENT file.

`_attach_probe_movie()` looked up candidates by the movie's canonical directory
and took `candidates[0]` unconditionally. When a movie has been renamed or
replaced and a probe entry for the old file still sits under the same prefix,
the row could be marked `verified` carrying the old file's audio/subtitle facts.

Reported by IdahoRC from a live Unraid deployment.

The episode path already filters candidates by filename; movies did not. Where
the row knows its own file (the Radarr construction path sets
`file_canonical_path` explicitly), that knowledge is now used.
"""

from __future__ import annotations


def _probe(path, langs=None):
    from subarr.media_probe import ProbeResult

    return ProbeResult(canonical_path=path)


def _movie(canonical="Movies/M (2024)", file_canonical=None):
    from subarr.coverage_engine import CoverageItem

    return CoverageItem(
        media_type="movie",
        title="M",
        canonical_path=canonical,
        file_canonical_path=file_canonical,
    )


CANON = "Movies/M (2024)"
CURRENT = "Movies/M (2024)/M (2024) Remux.mkv"
STALE = "Movies/M (2024)/M (2024) OLD 720p.mkv"


def test_stale_candidate_is_not_attached_when_the_row_knows_its_file():
    """The reported case. The stale entry sorts first, so `candidates[0]` would
    attach the wrong file's probe."""
    from subarr.coverage_engine import _attach_probe_movie

    item = _movie(file_canonical=CURRENT)
    idx = {CANON: [(STALE, _probe(STALE)), (CURRENT, _probe(CURRENT))]}
    _attach_probe_movie(item, idx, failed_idx={})

    assert item.file_canonical_path == CURRENT, "must not be repointed at the stale file"
    assert item.verification_state == "verified"


def test_row_is_left_unprobed_when_no_candidate_matches_its_file():
    """Only the old file has been probed. Attaching it would describe a file that
    is no longer there, so the row stays unprobed and can be probed properly."""
    from subarr.coverage_engine import _attach_probe_movie

    item = _movie(file_canonical=CURRENT)
    idx = {CANON: [(STALE, _probe(STALE))]}
    _attach_probe_movie(item, idx, failed_idx={})

    assert item.file_canonical_path == CURRENT, "must keep its own file path"
    assert item.verification_state == "unprobed", (
        "a probe for a different file must not mark this row verified"
    )


def test_unchanged_when_the_row_does_not_know_its_file():
    """The Bazarr-wanted construction path does not set file_canonical_path.
    With nothing to match against, the first candidate still wins exactly as
    before, so this fix does not change that path."""
    from subarr.coverage_engine import _attach_probe_movie

    item = _movie(file_canonical=None)
    idx = {CANON: [(STALE, _probe(STALE)), (CURRENT, _probe(CURRENT))]}
    _attach_probe_movie(item, idx, failed_idx={})

    assert item.file_canonical_path == STALE
    assert item.verification_state == "verified"


def test_single_matching_candidate_still_verifies():
    """The ordinary case must keep working: one probe, and it is this file."""
    from subarr.coverage_engine import _attach_probe_movie

    item = _movie(file_canonical=CURRENT)
    idx = {CANON: [(CURRENT, _probe(CURRENT))]}
    _attach_probe_movie(item, idx, failed_idx={})

    assert item.file_canonical_path == CURRENT
    assert item.verification_state == "verified"


def test_probe_failed_still_reported_for_a_known_file():
    """A recorded probe FAILURE is a different state from a mismatched probe and
    must still surface, otherwise the row silently reads unprobed forever."""
    from subarr.coverage_engine import _attach_probe_movie

    item = _movie(file_canonical=CURRENT)
    _attach_probe_movie(item, {}, failed_idx={CANON: [CURRENT]})

    assert item.verification_state == "probe_failed"
