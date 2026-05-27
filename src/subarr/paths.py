"""Path translation between the GUI's canonical form and the filesystem.

Four representations exist (see docs/design-notes.md). The GUI holds the canonical
form — relative-to-media-root, forward-slash, no leading slash — and converts at
boundaries.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath

from .config import settings


VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".webm", ".ts"}


class PathOutsideRootError(ValueError):
    """The requested path escapes media_root via .. or symlinks."""


def canonical_to_fs(canonical: str) -> Path:
    """Resolve canonical (e.g. 'TV/Show/Season 1') to an absolute filesystem path,
    guarding against traversal. Empty string means the media root itself."""
    rel = PurePosixPath(canonical.strip().strip("/"))
    if any(part == ".." for part in rel.parts):
        raise PathOutsideRootError(canonical)

    root = settings.media_root.resolve()
    target = (root / Path(*rel.parts)).resolve()
    try:
        target.relative_to(root)
    except ValueError as e:
        raise PathOutsideRootError(canonical) from e
    return target


def fs_to_canonical(p: Path) -> str:
    """Inverse of canonical_to_fs for a path known to be under media_root."""
    rel = p.resolve().relative_to(settings.media_root.resolve())
    return rel.as_posix()


def canonical_to_subgen_batch(canonical: str) -> str:
    """Form the `directory` query value subgen's /batch expects.
    Subgen sees the library at /media/library/ regardless of host mount."""
    rel = canonical.strip().strip("/")
    return f"/media/library/{rel}" if rel else "/media/library/"
