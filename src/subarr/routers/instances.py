"""#161 Phase 4A — instance + topology config API.

Exposes the multi-instance model (already built in Phase 1-3) for management:
list/test/add/edit/remove Sonarr/Radarr/Bazarr instances and view/override the
resolved library->arr/Bazarr/Plex-section topology. Persists to the config
override store and applies changes live via rebuild_instances + a runtime
client rebuild — no restart.

Config (settings/rebuild_*) is imported function-locally so the running config
module is re-read each call — this matches the reload-safe convention used by
admin.py/onboarding.py and keeps the reload-based test fixtures working.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..integrations import IntegrationError
from ..integrations.bazarr import BazarrClient
from ..integrations.radarr import RadarrClient
from ..integrations.sonarr import SonarrClient

router = APIRouter(prefix="/api", tags=["instances"])
log = logging.getLogger(__name__)

_ARR_SERVICES = ("sonarr", "radarr", "bazarr")
_CTORS = {"sonarr": SonarrClient, "radarr": RadarrClient, "bazarr": BazarrClient}


def _serialize(inst) -> dict:
    """Public instance view — NEVER leak the api_key; expose only presence."""
    return {
        "id": inst.id,
        "service": inst.service,
        "name": inst.name,
        "url": inst.url,
        "is_default": inst.id == "",
        "has_api_key": bool(inst.api_key),
    }


@router.get("/instances")
async def list_instances() -> dict:
    from ..config import settings

    return {"instances": [_serialize(i) for i in settings.instances]}


class TestConnRequest(BaseModel):
    service: str
    url: str
    api_key: str


async def _probe_connection(service: str, url: str, api_key: str) -> dict:
    """Construct a throwaway client and hit a cheap authenticated endpoint.
    Always closes the client (no leaked httpx session)."""
    client = _CTORS[service](base_url=url, api_key=api_key)
    try:
        if service in ("sonarr", "radarr"):
            folders = await client.root_folders()
            paths = [f.get("path") for f in folders if isinstance(f, dict) and f.get("path")]
            return {"ok": True, "detail": "connected", "root_folders": paths}
        # bazarr: list_tasks is a cheap authenticated GET
        await client.list_tasks()
        return {"ok": True, "detail": "connected", "root_folders": []}
    except IntegrationError as e:
        return {"ok": False, "detail": str(e), "root_folders": []}
    finally:
        await client.aclose()


@router.post("/instances/test")
async def test_connection(req: TestConnRequest) -> dict:
    if req.service not in _ARR_SERVICES:
        raise HTTPException(422, detail=f"unknown service {req.service!r}")
    if not req.url or not req.api_key:
        raise HTTPException(422, detail="url and api_key are required")
    return await _probe_connection(req.service, req.url, req.api_key)
