"""Sidecar basename mismatch detector (#203).

Walks a media directory and pairs each .srt with its target video. Two
outcomes that matter:

  - EXACT_MATCH:  video.mkv + video.en.srt   → fine, no action.
  - LOOSE_MATCH:  video.mkv + Video.EN.srt   → mismatched basename
                                              (or different case),
                                              Bazarr may not pair them.
  - ORPHAN:       solo.srt with no plausible video sibling.

Why it matters: Bazarr's sidecar detection is exact-basename-match
(case-sensitive on Linux). A small typo in the .srt filename — common
when subgen output collides with a manual rename, or when the video
was renamed AFTER the .srt was written — leaves the .srt invisible to
Bazarr forever. The row stays "wanted" even though disk has the sub.

This module identifies those mismatches and (optionally, via a separate
apply endpoint) renames them to the exact-match form so Bazarr's next
scan picks them up.

Similarity: we use SequenceMatcher.ratio() on the normalised stems
(lowercase, dots/underscores/spaces collapsed). Match threshold of
0.85 catches typical mismatches (case differences, trailing language
tags, extra dots) without dragging unrelated subs into the loose bucket.
"""

from __future__ import annotations

import logging
import re
from .error_detail import safe_error
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)


# Trailing language-tag patterns Bazarr/subgen strip when matching.
# We normalise these out before comparing video and srt stems.
_LANG_SUFFIX_RE = re.compile(
    r"\.(en|eng|english|fre|fr|fra|ja|jpn|ko|kor|zh|chi|cmn|"
    r"es|spa|de|ger|deu|it|ita|pt|por|ru|rus|hi|hin|ar|ara|"
    r"forced|sdh|cc)+$",
    re.IGNORECASE,
)


VIDEO_EXTS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm", ".ts", ".wmv"}


@dataclass
class SidecarMismatch:
    """One mismatch finding. Suggested rename takes the .srt path's
    parent + the video's stem + the same language tag, so renames are
    reversible just by undoing the basename swap."""

    srt_path: str  # full path of the .srt sidecar
    srt_basename: str  # display basename
    suggested_video: str | None  # full path of the matched video (None for orphans)
    suggested_rename_to: str | None  # full path the .srt should move to
    similarity: float  # 0.0 - 1.0; 1.0 = exact stem match (case-insensitive)
    reason: str  # "case-mismatch" | "trailing-tag" | "stem-typo" | "orphan"


@dataclass
class ScanResult:
    """Top-level scan output. Counters separated from mismatch list so
    the UI can show 'N/M srt files checked, K mismatches' cheaply."""

    root: str
    total_srt: int = 0
    exact_matches: int = 0
    loose_matches: list[SidecarMismatch] = field(default_factory=list)
    orphans: list[SidecarMismatch] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "total_srt": self.total_srt,
            "exact_matches": self.exact_matches,
            "loose_matches": [m.__dict__ for m in self.loose_matches],
            "orphans": [m.__dict__ for m in self.orphans],
            "errors": self.errors,
        }


def _normalise_stem(stem: str) -> str:
    """Lowercase, strip trailing language/forced/sdh tags, collapse
    non-alphanumeric runs. Two stems normalising to the same string are
    treated as exact matches for pairing purposes."""
    lower = stem.lower()
    while True:
        stripped = _LANG_SUFFIX_RE.sub("", lower)
        if stripped == lower:
            break
        lower = stripped
    return re.sub(r"[^a-z0-9]+", " ", lower).strip()


def _classify(video_stem: str, srt_stem: str) -> tuple[float, str]:
    """Return (similarity, reason) for a video-srt stem pair."""
    if video_stem == srt_stem:
        # Identical raw stems — caller should never reach here because
        # this would be EXACT_MATCH. Defensive 1.0.
        return 1.0, "exact"
    v_norm = _normalise_stem(video_stem)
    s_norm = _normalise_stem(srt_stem)
    if v_norm == s_norm:
        # After normalising, stems are identical — case or trailing-tag
        # difference. This is the highest-value mismatch class: trivial
        # rename, near-100% confidence.
        if video_stem != srt_stem and video_stem.lower() == srt_stem.lower():
            return 0.99, "case-mismatch"
        return 0.99, "trailing-tag"
    ratio = SequenceMatcher(None, v_norm, s_norm).ratio()
    return ratio, "stem-typo"


def scan(root: Path, loose_threshold: float = 0.85, max_depth: int | None = None) -> ScanResult:
    """Walk `root` and classify every .srt found.

    Args:
        root: directory to walk. Symlinks NOT followed.
        loose_threshold: similarity floor for loose matches. Below this,
            an .srt with no exact-stem video sibling is an orphan.
        max_depth: cap recursion. None = unlimited. Use this to keep
            walk-time bounded on huge libraries.
    """
    result = ScanResult(root=str(root))
    if not root.exists() or not root.is_dir():
        result.errors.append(f"root not a directory: {root}")
        return result

    # Walk depth-first via Path.rglob with a depth cap implemented as
    # a Path-component count check. Cheap and correct.
    root_depth = len(root.parts)
    for srt_path in root.rglob("*.srt"):
        # Skip symlinks and depth-exceeded paths.
        try:
            if srt_path.is_symlink():
                continue
            if max_depth is not None and (len(srt_path.parts) - root_depth) > max_depth:
                continue
        except OSError as e:
            result.errors.append(f"{srt_path}: {safe_error(e)}")
            continue
        result.total_srt += 1
        try:
            mismatch = _classify_one(srt_path, loose_threshold)
        except OSError as e:
            result.errors.append(f"{srt_path}: {safe_error(e)}")
            continue
        if mismatch is None:
            result.exact_matches += 1
        elif mismatch.suggested_video is None:
            result.orphans.append(mismatch)
        else:
            result.loose_matches.append(mismatch)

    # Sort mismatches by similarity desc (highest-confidence renames first).
    result.loose_matches.sort(key=lambda m: -m.similarity)
    return result


def _siblings(srt_path: Path) -> Iterable[Path]:
    """Sibling video files in the same directory, sorted for determinism."""
    try:
        return sorted(p for p in srt_path.parent.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS)
    except OSError:
        return []


def _classify_one(srt_path: Path, loose_threshold: float) -> SidecarMismatch | None:
    """None = exact match (.srt matches a video sibling). Otherwise return
    a SidecarMismatch describing what's off."""
    srt_stem = srt_path.stem
    # The leading part of the stem before any language tag is the "video
    # stem candidate" — what subgen's <video>.en.srt convention strips.
    srt_video_stem = _LANG_SUFFIX_RE.sub("", srt_stem)
    for vid in _siblings(srt_path):
        vid_stem = vid.stem
        if vid_stem == srt_video_stem:
            # Exact match against the bare video stem. Subgen's <video>.en.srt
            # pattern is what Bazarr expects too.
            return None
    # No exact-stem sibling. Find the best fuzzy match.
    best_sim = 0.0
    best_video: Path | None = None
    best_reason = "orphan"
    for vid in _siblings(srt_path):
        sim, reason = _classify(vid.stem, srt_video_stem)
        if sim > best_sim:
            best_sim = sim
            best_video = vid
            best_reason = reason
    if best_video is None or best_sim < loose_threshold:
        return SidecarMismatch(
            srt_path=str(srt_path),
            srt_basename=srt_path.name,
            suggested_video=None,
            suggested_rename_to=None,
            similarity=best_sim,
            reason="orphan",
        )
    # Loose match: suggest renaming the .srt to match the video's stem
    # while preserving the .en.srt language tag (if present) and extension.
    tag_part = srt_stem[len(srt_video_stem) :]  # e.g. ".en" or ""
    suggested_basename = f"{best_video.stem}{tag_part}{srt_path.suffix}"
    suggested_path = srt_path.parent / suggested_basename
    return SidecarMismatch(
        srt_path=str(srt_path),
        srt_basename=srt_path.name,
        suggested_video=str(best_video),
        suggested_rename_to=str(suggested_path),
        similarity=best_sim,
        reason=best_reason,
    )


def apply_rename(srt_path: Path, suggested_rename_to: Path) -> None:
    """Apply a single rename. Raises OSError on conflicts; caller catches
    and surfaces. Will NOT overwrite an existing destination file."""
    if not srt_path.exists():
        raise FileNotFoundError(f"source missing: {srt_path}")
    if suggested_rename_to.exists():
        raise FileExistsError(f"target already exists: {suggested_rename_to}")
    if srt_path.parent != suggested_rename_to.parent:
        raise ValueError(
            f"rename must stay in the same directory (got {srt_path.parent} → {suggested_rename_to.parent})"
        )
    srt_path.rename(suggested_rename_to)
    log.info("sidecar renamed: %s → %s", srt_path, suggested_rename_to)
