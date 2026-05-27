"""GET /api/browse?path=<canonical> — lazy folder + file tree.

Folders are returned first (sorted by name), then video files (sorted by
name) as leaf entries the user can individually select for scanning.
Subgen's /batch handler accepts both directories and single files (the
v4.1-patched transcribe_existing branch handles `os.path.isfile(path)`),
so a leaf-file checkbox queues just that one file.

Existing-srt detection per file: an entry's `has_sibling_srt` is True if
any `<basename>.*.srt` exists in the same directory. That lets the UI
gray out files Bazarr/subgen already wrote subs for.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..paths import VIDEO_EXTS, PathOutsideRootError, canonical_to_fs, fs_to_canonical

router = APIRouter(prefix="/api", tags=["browse"])


class BrowseEntry(BaseModel):
    name: str
    path: str  # canonical (forward slashes, no leading /, relative to media_root)
    is_dir: bool
    # Folder-only fields (zero for files):
    video_count: int = 0
    srt_count: int = 0
    # File-only fields (false for dirs):
    has_sibling_srt: bool = False
    size_mb: float | None = None


class BrowseResponse(BaseModel):
    path: str
    parent: str | None
    entries: list[BrowseEntry]


@router.get("/browse", response_model=BrowseResponse)
def browse(path: str = Query("", description="Canonical path relative to media root")) -> BrowseResponse:
    try:
        target = canonical_to_fs(path)
    except PathOutsideRootError:
        raise HTTPException(400, detail=f"path escapes media root: {path!r}")

    if not target.exists():
        raise HTTPException(404, detail=f"not found: {path!r}")
    if not target.is_dir():
        raise HTTPException(400, detail=f"not a directory: {path!r}")

    try:
        all_children = list(target.iterdir())
    except PermissionError:
        raise HTTPException(403, detail=f"permission denied: {path!r}")

    # Build a set of srt basenames (e.g. {"S01E01", "S01E02.en"}) so we can
    # detect, for each video file, whether any sibling srt has its basename
    # as a prefix. Cheap O(n) scan of the same directory.
    srt_stems: set[str] = set()
    for child in all_children:
        if child.is_file() and child.suffix.lower() == ".srt":
            srt_stems.add(child.stem)  # 'Foo.en' for Foo.en.srt

    def _has_sibling_srt(video_path: Path) -> bool:
        vid_stem = video_path.stem  # 'Foo' for Foo.mkv
        return any(s == vid_stem or s.startswith(vid_stem + ".") for s in srt_stems)

    # Folders first (alpha), then video files (alpha). Sort key keeps types
    # grouped without re-iterating.
    dirs: list[Path] = []
    files: list[Path] = []
    for child in all_children:
        if child.name.startswith("."):
            continue
        if child.is_dir():
            dirs.append(child)
        elif child.is_file() and child.suffix.lower() in VIDEO_EXTS:
            files.append(child)
    dirs.sort(key=lambda p: p.name.lower())
    files.sort(key=lambda p: p.name.lower())

    entries: list[BrowseEntry] = []
    for d in dirs:
        video_count, srt_count = _count_media(d)
        entries.append(BrowseEntry(
            name=d.name,
            path=fs_to_canonical(d),
            is_dir=True,
            video_count=video_count,
            srt_count=srt_count,
        ))
    for f in files:
        try:
            size_mb = round(f.stat().st_size / (1024 * 1024), 1)
        except OSError:
            size_mb = None
        entries.append(BrowseEntry(
            name=f.name,
            path=fs_to_canonical(f),
            is_dir=False,
            has_sibling_srt=_has_sibling_srt(f),
            size_mb=size_mb,
        ))

    canonical = path.strip().strip("/")
    parent = None
    if canonical:
        parent = "/".join(canonical.split("/")[:-1])

    return BrowseResponse(path=canonical, parent=parent, entries=entries)


def _count_media(folder: Path) -> tuple[int, int]:
    """Shallow count of video + srt files directly in `folder`. Cheap; lazy load."""
    video = srt = 0
    try:
        for entry in folder.iterdir():
            if not entry.is_file():
                continue
            ext = entry.suffix.lower()
            if ext in VIDEO_EXTS:
                video += 1
            elif ext == ".srt":
                srt += 1
    except (PermissionError, OSError):
        return 0, 0
    return video, srt
