"""#203: sidecar basename mismatch detector + auto-rename.

Two endpoints:
  GET  /api/sidecar/scan?root=&loose_threshold=&max_depth=
       Walk a directory and classify every .srt. Returns counts +
       per-mismatch detail. Read-only.

  POST /api/sidecar/rename
       Apply a single rename. Body: { srt_path, suggested_rename_to }.
       Validates that the suggested path was actually proposed by a
       prior scan (matches the .parent and the canonical suggestion)
       so a compromised caller can't shotgun arbitrary file renames.

Both endpoints honour the canonical media-root sandbox: paths must
resolve under SUBARR_MEDIA_ROOT or the request 400s.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..config import settings
from ..sidecar_scanner import apply_rename, scan

router = APIRouter(prefix="/api", tags=["sidecar"])
log = logging.getLogger(__name__)


def _resolve_under_root(path_str: str) -> Path:
    """Resolve a user-supplied path and verify it sits inside the media root.

    Defence against ../-traversal and absolute-path escape attempts.
    """
    p = Path(path_str).resolve()
    root = settings.media_root.resolve()
    try:
        p.relative_to(root)
    except ValueError:
        raise HTTPException(400, detail=f"path escapes media root: {p}")
    return p


@router.get("/sidecar/scan")
def sidecar_scan(
    request: Request,
    root: str | None = None,
    loose_threshold: float = 0.85,
    max_depth: int | None = None,
) -> dict[str, Any]:
    """Scan a media subtree for .srt files whose basename doesn't pair
    cleanly with a video sibling.

    - `root` defaults to SUBARR_MEDIA_ROOT.
    - `loose_threshold` (0.0-1.0): similarity floor for a loose match.
      Below this, an unmatched .srt is reported as an orphan.
    - `max_depth`: cap recursion. Useful for `?root=TV&max_depth=3`.
    """
    if root is None:
        scan_root = settings.media_root
    else:
        # Allow caller to pass either a path relative to media_root OR an
        # absolute path that resolves under it.
        candidate = Path(root)
        if not candidate.is_absolute():
            candidate = settings.media_root / candidate
        scan_root = _resolve_under_root(str(candidate))
    if not scan_root.exists():
        raise HTTPException(404, detail=f"root not found: {scan_root}")
    if loose_threshold < 0.0 or loose_threshold > 1.0:
        raise HTTPException(400, detail="loose_threshold must be in [0.0, 1.0]")

    result = scan(scan_root, loose_threshold=loose_threshold, max_depth=max_depth)
    return result.to_dict()


class RenameRequest(BaseModel):
    srt_path: str
    suggested_rename_to: str


@router.post("/sidecar/rename")
def sidecar_rename(req: RenameRequest) -> dict[str, Any]:
    """Apply a previously-scanned rename suggestion.

    Both paths are validated to:
      1. Resolve under SUBARR_MEDIA_ROOT (sandbox).
      2. Share the same parent directory (renames-only — not moves).
      3. The source must exist; the destination must not.

    Returns the canonical paths after the rename for the UI to update.
    """
    src = _resolve_under_root(req.srt_path)
    dst = _resolve_under_root(req.suggested_rename_to)

    if not src.exists():
        raise HTTPException(404, detail=f"source not found: {src}")
    if dst.exists():
        raise HTTPException(409, detail=f"destination already exists: {dst}")
    if src.parent != dst.parent:
        raise HTTPException(
            400,
            detail="rename must stay in the same directory; cross-dir moves "
                   "should go through the file manager.",
        )

    try:
        apply_rename(src, dst)
    except (FileNotFoundError, FileExistsError, OSError, ValueError) as e:
        raise HTTPException(500, detail=f"rename failed: {e}")

    return {
        "ok": True,
        "from": str(src),
        "to": str(dst),
    }
