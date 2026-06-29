"""GET /api/languages — the full Whisper detection set for the manual
audio-language picker. Single source: langs.WHISPER_LANGUAGES (#358)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..langs import WHISPER_LANGUAGES

router = APIRouter(prefix="/api", tags=["languages"])


@router.get("/languages")
async def list_languages() -> dict[str, Any]:
    langs = sorted(
        ({"code": c, "name": n} for c, n in WHISPER_LANGUAGES.items()),
        key=lambda x: x["name"],
    )
    return {"count": len(langs), "languages": langs}
