"""#560: readable video formats outside the old seven-extension list sat in
Coverage's "Analyzing" bucket forever.

`paths.VIDEO_EXTS` had no `.wmv`, `.mpg` or `.mpeg`, although subgen transcribes
all of them and ffprobe reads them. Eager-probe skipped those files, and because
they were not in `UNSUPPORTED_EXTS` either, nothing ever gave them a terminal
state. `sidecar_scanner` also kept its own, different copy of the list.
"""

from __future__ import annotations

import pytest

# Formats subgen's VIDEO_EXTENSIONS accepts that Sonarr/Radarr commonly import.
READABLE = [".wmv", ".mpg", ".mpeg", ".m2ts", ".asf", ".flv", ".ogv", ".3gp", ".divx", ".f4v"]


@pytest.mark.parametrize("ext", READABLE)
def test_readable_formats_are_eager_probed(ext):
    from subarr.coverage_cache import eager_probe_targets

    path = f"TV/Example/Season 05/Example - S05E01{ext}"
    assert eager_probe_targets([{"verification_state": "unprobed", "file_canonical_path": path}]) == [path]


def test_reporters_minimal_case():
    from subarr.coverage_cache import eager_probe_targets

    row = {
        "verification_state": "unprobed",
        "file_canonical_path": "TV/Example/Season 05/Example - S05E01.wmv",
    }
    assert eager_probe_targets([row]) == ["TV/Example/Season 05/Example - S05E01.wmv"]


def test_extension_match_is_case_insensitive():
    from subarr.coverage_cache import eager_probe_targets

    row = {"verification_state": "unprobed", "file_canonical_path": "TV/X/X - S01E01.WMV"}
    assert eager_probe_targets([row]) == ["TV/X/X - S01E01.WMV"]


def test_sidecar_scanner_uses_the_shared_list():
    """One list, not two copies that drift apart."""
    import inspect

    from subarr import paths, sidecar_scanner

    # Contents, not identity: other suites reload `subarr.paths`, which gives it
    # a new set object while sidecar_scanner keeps the one it imported.
    assert sidecar_scanner.VIDEO_EXTS == paths.VIDEO_EXTS
    src = inspect.getsource(sidecar_scanner)
    assert "from .paths import VIDEO_EXTS" in src
    assert "VIDEO_EXTS = {" not in src


def test_every_listed_format_is_one_subgen_accepts():
    """subarr must never schedule a file subgen will refuse as not-a-video.
    Mirrors subgen's VIDEO_EXTENSIONS (upstream subgen.py)."""
    from subarr.paths import VIDEO_EXTS

    subgen_video = {
        ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".mpg", ".mpeg",
        ".3gp", ".ogv", ".vob", ".rm", ".rmvb", ".ts", ".m4v", ".f4v", ".svq3",
        ".asf", ".m2ts", ".divx", ".xvid",
    }  # fmt: skip
    assert VIDEO_EXTS <= subgen_video, sorted(VIDEO_EXTS - subgen_video)


def _ep(file_path):
    from subarr.coverage_engine import CoverageItem

    return CoverageItem(
        media_type="episode",
        title="Show",
        canonical_path="TV/Show",
        episode_number="1x1",
        file_canonical_path=file_path,
    )


def test_a_resolved_file_in_an_unknown_format_ends_unsupported_not_analyzing():
    from subarr.coverage_engine import _disqualify_unsupported

    odd = _ep("TV/Show/Show - S01E01.strm")
    _disqualify_unsupported([odd])
    assert odd.verification_state == "unsupported"


def test_newly_listed_formats_are_not_disqualified():
    from subarr.coverage_engine import _disqualify_unsupported

    wmv = _ep("TV/Show/Show - S01E01.wmv")
    _disqualify_unsupported([wmv])
    assert wmv.verification_state == "unprobed"


def test_a_folder_with_a_dot_in_its_name_is_not_a_file_format():
    """`Mr. Robot` has the suffix `. Robot`. Only a resolved FILE path is judged
    by its extension; an unresolved folder row is a separate resolution gap."""
    from subarr.coverage_engine import CoverageItem, _disqualify_unsupported

    folder = CoverageItem(
        media_type="episode", title="Mr. Robot", canonical_path="TV/Mr. Robot", episode_number="1x1"
    )
    _disqualify_unsupported([folder])
    assert folder.verification_state == "unprobed"


def test_a_verified_row_is_never_downgraded():
    from subarr.coverage_engine import _disqualify_unsupported

    odd = _ep("TV/Show/Show - S01E01.strm")
    odd.verification_state = "verified"
    _disqualify_unsupported([odd])
    assert odd.verification_state == "verified"
