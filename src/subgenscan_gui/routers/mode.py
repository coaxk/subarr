"""GET /api/mode — reads subgen's compose.yaml and returns current mode.

Same detection mechanism as Subgenscan.ps1 V69's Get-CurrentMode: regex on the
top-level patience value inside SUBGEN_KWARGS. We deliberately do NOT parse the
JSON-in-YAML string — the PS script's regex is the canonical contract and we
match it exactly so the two tools never disagree.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import settings

router = APIRouter(prefix="/api", tags=["mode"])


# Match patience value inside SUBGEN_KWARGS only (not per-language overrides).
# The PS script uses "patience.: 1.5" / "patience.: 1.0" against the whole file,
# but our SUBGEN_KWARGS line is unique enough to scope this safely.
_KWARGS_LINE_RE = re.compile(r"SUBGEN_KWARGS:\s*['\"]?(.+?)['\"]?\s*$", re.MULTILINE)
_PATIENCE_RE = re.compile(r'"patience":\s*([0-9.]+)')
_LENGTH_PENALTY_RE = re.compile(r'"length_penalty":\s*([0-9.]+)')


class ModeResponse(BaseModel):
    mode: str  # "european" | "japanese" | "unknown"
    patience: float | None
    length_penalty: float | None
    compose_path: str


def _detect(compose_text: str) -> tuple[str, float | None, float | None]:
    m = _KWARGS_LINE_RE.search(compose_text)
    if not m:
        return "unknown", None, None
    kwargs_blob = m.group(1)
    pat = _PATIENCE_RE.search(kwargs_blob)
    lp = _LENGTH_PENALTY_RE.search(kwargs_blob)
    patience = float(pat.group(1)) if pat else None
    length_penalty = float(lp.group(1)) if lp else None

    if patience is None:
        mode = "unknown"
    elif abs(patience - 1.5) < 1e-6:
        mode = "european"
    elif abs(patience - 1.0) < 1e-6:
        mode = "japanese"
    else:
        mode = "unknown"
    return mode, patience, length_penalty


router_path = settings.subgen_compose_path


@router.get("/mode", response_model=ModeResponse)
def get_mode() -> ModeResponse:
    path: Path = settings.subgen_compose_path
    if not path.exists():
        raise HTTPException(503, detail=f"subgen compose not found at {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(503, detail=f"could not read subgen compose: {e}")

    mode, patience, length_penalty = _detect(text)
    return ModeResponse(
        mode=mode,
        patience=patience,
        length_penalty=length_penalty,
        compose_path=str(path),
    )
