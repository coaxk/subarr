"""GET /api/updates — cached GitHub-release state for tracked products.

The UI hits this to render:
  - Header pill (soft violet dot when any product has has_update=true)
  - Home dashboard tile (one-liner "Update available · view")
  - Settings → Updates panel (full details + copy-paste compose recipe)

This endpoint is fast (single SQLite read) — actual polling happens
in the background via UpdateChecker. Use POST /api/updates/refresh to
force a re-poll (e.g. when the user clicks "check now" in Settings).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api")


@router.get("/updates")
def get_updates(request: Request) -> dict[str, Any]:
    checker = getattr(request.app.state, "update_checker", None)
    if checker is None:
        return {"products": [], "any_update_available": False, "enabled": False}
    states = checker.states()
    return {
        "enabled": True,
        "products": [s.to_dict() for s in states],
        "any_update_available": any(s.has_update for s in states),
    }


@router.post("/updates/refresh")
async def refresh_updates(request: Request) -> dict[str, Any]:
    """Force-poll GitHub immediately. Used by Settings → 'Check now'."""
    checker = getattr(request.app.state, "update_checker", None)
    if checker is None:
        return {"products": [], "refreshed": False, "reason": "checker disabled"}
    states = await checker.refresh_now()
    return {
        "refreshed": True,
        "products": [s.to_dict() for s in states],
        "any_update_available": any(s.has_update for s in states),
    }
