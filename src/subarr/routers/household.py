"""v1.1-L: per-user language profile foundation (Tautulli).

GET /api/household/users        — list of Plex users known to Tautulli
GET /api/household/profile      — aggregated household language preferences

For v1.1 we ship the users + watch-stats foundation. The deep per-user
language scoring (cross-correlate each user's history with Sonarr/Radarr
originalLanguage to build {user: {lang: weight}}) ships in v1.1.1 once
we have an efficient way to enrich rating_keys with metadata.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, Request

from ..integrations import IntegrationError

router = APIRouter(prefix="/api/household", tags=["household"])
log = logging.getLogger(__name__)


@router.get("/users")
async def list_users(request: Request) -> dict[str, Any]:
    t = request.app.state.integrations.tautulli
    if not t.is_configured():
        return {"available": False, "users": []}
    try:
        users = await t.get_users()
    except IntegrationError as e:
        log.warning("get_users failed: %s", e)
        return {"available": False, "users": [], "error": str(e)}
    # Filter to non-system users
    cleaned = [
        {
            "user_id": u.get("user_id"),
            "username": u.get("username"),
            "friendly_name": u.get("friendly_name"),
            "email": u.get("email"),
        }
        for u in users
        if u.get("user_id") and u.get("username") not in ("Local", "")
    ]
    return {"available": True, "count": len(cleaned), "users": cleaned}


@router.get("/profile")
async def household_profile(
    request: Request,
    days: int = Query(30, description="Lookback window for watch stats"),
) -> dict[str, Any]:
    """v1.1-L foundation: per-user watch-time stats over the window.
    Scoring integration follows in v1.1.1."""
    t = request.app.state.integrations.tautulli
    if not t.is_configured():
        return {"available": False, "members": []}
    try:
        users = await t.get_users()
    except IntegrationError as e:
        return {"available": False, "members": [], "error": str(e)}
    members = []
    for u in users:
        uid = u.get("user_id")
        if not uid or u.get("username") in ("Local", ""):
            continue
        try:
            stats = await t.get_user_watch_time_stats(uid, query_days=days)
        except IntegrationError:
            stats = []
        members.append(
            {
                "user_id": uid,
                "username": u.get("username"),
                "friendly_name": u.get("friendly_name"),
                "watch_stats": stats,
            }
        )
    return {"available": True, "window_days": days, "members": members}
