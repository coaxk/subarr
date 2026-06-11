"""Unit tests for queue._infer_skip_reason (#89 slice).

subgen returns a single 'skipped' counter regardless of which should_skip_file
branch fired. subarr's _infer_skip_reason re-derives the most common reason
from locally-inspectable on-disk data so the "Issues — silent fails" bucket
isn't a wall of unexplained 'unknown' chips.

Before this slice the helper only matched an external `.srt` sidecar. subgen
(has_external_subtitle_in_language) actually treats a wider extension set as an
existing subtitle: {.srt, .vtt, .sub, .ass, .ssa, .idx, .sbv, .pgs, .ttml,
.lrc}. So a file subgen skipped because an `.ass`/`.vtt`/etc. sidecar already
exists was mislabeled 'unknown'. These tests pin the broadened sidecar match
while keeping the directory / no-sidecar / wrong-stem cases as 'unknown'.
"""

from __future__ import annotations

import types

import pytest

from subarr import paths
from subarr.libraries import Library
from subarr.routers.queue import _infer_skip_reason


@pytest.fixture
def media_tree(tmp_path, monkeypatch):
    """Point the media root at a tmp tree with one episode file.

    settings is a frozen dataclass, so instead of mutating it we swap the
    `settings` reference that paths.canonical_to_fs resolves against for a
    lightweight stub. #134 Phase 1: canonical_to_fs resolves via
    settings.libraries, so the stub carries a single default library (slug
    "") rooted at the tmp tree.

    Returns (root, show_dir). Tests drop sidecars next to ep.mkv and assert
    the inferred skip reason for canonical path 'TV/Show/ep.mkv'.
    """
    root = tmp_path / "media"
    show = root / "TV" / "Show"
    show.mkdir(parents=True)
    (show / "ep.mkv").write_bytes(b"")
    stub = types.SimpleNamespace(
        media_root=root,
        libraries=(
            Library(slug="", name="default", fs_root=root, subgen_prefix="/media", arr_prefix="/data/Media/"),
        ),
    )
    monkeypatch.setattr(paths, "settings", stub)
    return root, show


CANON = "TV/Show/ep.mkv"


def test_external_srt_sidecar_classified_sub_exists(media_tree):
    """Existing behavior: a matching .srt next to the file → 'sub_exists'."""
    _root, show = media_tree
    (show / "ep.en.srt").write_text("1\n")
    assert _infer_skip_reason(CANON) == "sub_exists"


def test_plain_srt_sidecar_classified_sub_exists(media_tree):
    """`<stem>.srt` (no lang tag) also counts as an existing sub."""
    _root, show = media_tree
    (show / "ep.srt").write_text("1\n")
    assert _infer_skip_reason(CANON) == "sub_exists"


@pytest.mark.parametrize("ext", [".ass", ".ssa", ".vtt", ".sub", ".idx", ".sbv", ".pgs", ".ttml", ".lrc"])
def test_non_srt_subtitle_sidecar_classified_sub_exists(media_tree, ext):
    """NEW (#89): subgen skips on any subtitle extension it recognizes, not
    just .srt. A matching `.ass`/`.vtt`/etc. sidecar must classify as
    'sub_exists', not 'unknown'."""
    _root, show = media_tree
    (show / f"ep.en{ext}").write_text("x\n")
    assert _infer_skip_reason(CANON) == "sub_exists"


def test_no_sidecar_is_unknown(media_tree):
    """Genuinely unexplained skip (e.g. audio-language match — not locally
    inspectable here) still falls through to 'unknown'."""
    _root, _show = media_tree
    assert _infer_skip_reason(CANON) == "unknown"


def test_unrelated_subtitle_does_not_false_positive(media_tree):
    """A subtitle for a DIFFERENT file in the same folder must not be
    attributed to this file — stem must match."""
    _root, show = media_tree
    (show / "other-episode.en.srt").write_text("1\n")
    assert _infer_skip_reason(CANON) == "unknown"


def test_directory_path_is_unknown(media_tree):
    """A canonical path pointing at a folder (season dir) can't be pinned to
    THE file subgen tripped on → 'unknown' (unchanged)."""
    assert _infer_skip_reason("TV/Show") == "unknown"


def test_path_outside_root_is_unknown(media_tree):
    """Traversal / outside-root path resolves to 'unknown' rather than
    raising."""
    assert _infer_skip_reason("../../etc/passwd") == "unknown"


def test_non_subtitle_sibling_is_not_matched(media_tree):
    """A same-stem non-subtitle file (e.g. a .nfo) must not be mistaken for
    an existing subtitle."""
    _root, show = media_tree
    (show / "ep.nfo").write_text("meta\n")
    assert _infer_skip_reason(CANON) == "unknown"
