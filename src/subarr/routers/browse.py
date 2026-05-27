"""GET /api/browse?path=<canonical> — lazy folder tree."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..paths import VIDEO_EXTS, PathOutsideRootError, canonical_to_fs, fs_to_canonical

router = APIRouter(prefix="/api", tags=["browse"])


class BrowseEntry(BaseModel):
    name: str
    path: str  # canonical
    is_dir: bool
    video_count: int = 0
    srt_count: int = 0


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

    entries: list[BrowseEntry] = []
    try:
        children = sorted(target.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower()))
    except PermissionError:
        raise HTTPException(403, detail=f"permission denied: {path!r}")

    for child in children:
        if child.name.startswith("."):
            continue
        if child.is_dir():
            video_count, srt_count = _count_media(child)
            entries.append(
                BrowseEntry(
                    name=child.name,
                    path=fs_to_canonical(child),
                    is_dir=True,
                    video_count=video_count,
                    srt_count=srt_count,
                )
            )

    canonical = path.strip().strip("/")
    parent = None
    if canonical:
        parent = "/".join(canonical.split("/")[:-1])

    return BrowseResponse(path=canonical, parent=parent, entries=entries)


def _count_media(folder) -> tuple[int, int]:
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
