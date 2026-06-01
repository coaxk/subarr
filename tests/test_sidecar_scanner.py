"""#203: sidecar basename mismatch detector + auto-rename tests."""
from __future__ import annotations

from pathlib import Path

import pytest


def _make_video(dir: Path, name: str) -> Path:
    p = dir / name
    p.write_bytes(b"\x00" * 16)  # need something on disk
    return p


def _make_srt(dir: Path, name: str) -> Path:
    p = dir / name
    p.write_text("1\n00:00:01,000 --> 00:00:02,000\nhello\n")
    return p


def test_exact_match_no_mismatch(tmp_path):
    from subarr.sidecar_scanner import scan
    _make_video(tmp_path, "Show.S01E01.mkv")
    _make_srt(tmp_path, "Show.S01E01.en.srt")
    result = scan(tmp_path)
    assert result.total_srt == 1
    assert result.exact_matches == 1
    assert result.loose_matches == []
    assert result.orphans == []


def test_case_mismatch_detected(tmp_path):
    """show.S01E01.mkv + SHOW.S01E01.en.srt — same basename, wrong case
    on Linux. Bazarr fails to pair them; we should flag + suggest rename."""
    from subarr.sidecar_scanner import scan
    _make_video(tmp_path, "show.S01E01.mkv")
    _make_srt(tmp_path, "SHOW.S01E01.en.srt")
    result = scan(tmp_path)
    assert result.exact_matches == 0
    assert len(result.loose_matches) == 1
    m = result.loose_matches[0]
    assert m.reason == "case-mismatch"
    assert m.similarity >= 0.95
    assert m.suggested_rename_to.endswith("show.S01E01.en.srt")


def test_stem_typo_detected(tmp_path):
    """Realistic typo: srt has extra dot before language tag."""
    from subarr.sidecar_scanner import scan
    _make_video(tmp_path, "Cheers.S01E01.mkv")
    _make_srt(tmp_path, "Cheers.S01E01..en.srt")  # double-dot
    result = scan(tmp_path)
    assert len(result.loose_matches) == 1
    m = result.loose_matches[0]
    assert m.similarity >= 0.85
    assert m.suggested_rename_to.endswith("Cheers.S01E01.en.srt")


def test_orphan_srt_no_video(tmp_path):
    """Solo .srt with no matching video sibling."""
    from subarr.sidecar_scanner import scan
    _make_srt(tmp_path, "Random.Sub.srt")
    result = scan(tmp_path)
    assert result.exact_matches == 0
    assert result.loose_matches == []
    assert len(result.orphans) == 1
    assert result.orphans[0].suggested_video is None
    assert result.orphans[0].reason == "orphan"


def test_recursion_into_subdirs(tmp_path):
    s1 = tmp_path / "Show" / "Season 1"
    s1.mkdir(parents=True)
    _make_video(s1, "Show.S01E01.mkv")
    _make_srt(s1, "Show.S01E01.en.srt")
    s2 = tmp_path / "Show" / "Season 2"
    s2.mkdir(parents=True)
    _make_video(s2, "Show.S02E01.mkv")
    _make_srt(s2, "SHOW.S02E01.en.srt")  # case mismatch in season 2
    from subarr.sidecar_scanner import scan
    result = scan(tmp_path)
    assert result.total_srt == 2
    assert result.exact_matches == 1
    assert len(result.loose_matches) == 1


def test_max_depth_caps_recursion(tmp_path):
    deep = tmp_path / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)
    _make_srt(deep, "deep.srt")
    from subarr.sidecar_scanner import scan
    # depth=2 covers root + a + b but stops there.
    result = scan(tmp_path, max_depth=2)
    assert result.total_srt == 0  # deep.srt is 4 levels in


def test_apply_rename_moves_file(tmp_path):
    from subarr.sidecar_scanner import apply_rename
    src = _make_srt(tmp_path, "WRONG.en.srt")
    dst = tmp_path / "right.en.srt"
    apply_rename(src, dst)
    assert not src.exists()
    assert dst.exists()


def test_apply_rename_refuses_overwrite(tmp_path):
    from subarr.sidecar_scanner import apply_rename
    src = _make_srt(tmp_path, "a.srt")
    dst = _make_srt(tmp_path, "b.srt")
    with pytest.raises(FileExistsError):
        apply_rename(src, dst)
    # Both still exist — refusal must be atomic.
    assert src.exists()
    assert dst.exists()


def test_apply_rename_refuses_cross_directory(tmp_path):
    from subarr.sidecar_scanner import apply_rename
    src_dir = tmp_path / "a"
    src_dir.mkdir()
    src = _make_srt(src_dir, "x.srt")
    dst = tmp_path / "x.srt"  # different parent
    with pytest.raises(ValueError):
        apply_rename(src, dst)
