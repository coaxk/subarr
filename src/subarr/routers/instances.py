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

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import config_store
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


def _extras() -> dict:
    raw = config_store.load_overrides().get("instances", {})
    return raw if isinstance(raw, dict) else {}


async def _apply_instances(request: Request, extras: dict) -> None:
    """Validate, persist the extras dict, rebuild settings.instances, rebuild the
    live bundle. Raises HTTPException(422) on invalid config (and does NOT
    persist) — a 422 beats rebuild_instances' silent fail-soft drop."""
    from ..config import rebuild_instances, settings
    from ..instances import Instance, InstanceConfigError, build_instances

    defaults = [
        Instance(
            id="", service="sonarr", name="default", url=settings.sonarr_url, api_key=settings.sonarr_api_key
        ),
        Instance(
            id="", service="radarr", name="default", url=settings.radarr_url, api_key=settings.radarr_api_key
        ),
        Instance(
            id="", service="bazarr", name="default", url=settings.bazarr_url, api_key=settings.bazarr_api_key
        ),
    ]
    try:
        build_instances(defaults, extras)
    except InstanceConfigError as e:
        raise HTTPException(422, detail=str(e))
    config_store.save_override("instances", extras)
    rebuild_instances(settings)
    from .onboarding import _rebuild_runtime_clients

    await _rebuild_runtime_clients(request.app.state)


class AddInstanceRequest(BaseModel):
    service: str
    name: str
    url: str
    api_key: str


@router.post("/instances", status_code=201)
async def add_instance(req: AddInstanceRequest, request: Request) -> dict:
    from ..config import settings
    from ..libraries import slugify

    if req.service not in _ARR_SERVICES:
        raise HTTPException(422, detail=f"unknown service {req.service!r}")
    if not (req.name and req.url and req.api_key):
        raise HTTPException(422, detail="name, url and api_key are required")
    new_id = slugify(req.name)
    extras = _extras()
    svc_list = list(extras.get(req.service, []))
    if any(slugify(i.get("name", "")) == new_id or i.get("slug") == new_id for i in svc_list):
        raise HTTPException(409, detail=f"{req.service} instance id {new_id!r} already exists")
    svc_list.append({"name": req.name, "url": req.url, "api_key": req.api_key, "slug": new_id})
    extras[req.service] = svc_list
    await _apply_instances(request, extras)
    inst = next(i for i in settings.instances if i.service == req.service and i.id == new_id)
    return _serialize(inst)


class EditInstanceRequest(BaseModel):
    name: str
    url: str
    api_key: str | None = None  # omitted/empty -> keep existing (masked-edit)


@router.put("/instances/{service}/{instance_id}")
async def edit_instance(service: str, instance_id: str, req: EditInstanceRequest, request: Request) -> dict:
    from ..config import settings
    from ..libraries import slugify

    if service not in _ARR_SERVICES:
        raise HTTPException(422, detail=f"unknown service {service!r}")
    if instance_id.strip() == "":
        raise HTTPException(400, detail="the default instance is edited via env/onboarding, not here")
    extras = _extras()
    svc_list = list(extras.get(service, []))
    idx = next(
        (n for n, i in enumerate(svc_list) if (i.get("slug") or slugify(i.get("name", ""))) == instance_id),
        None,
    )
    if idx is None:
        raise HTTPException(404, detail=f"{service} instance {instance_id!r} not found")
    existing = svc_list[idx]
    svc_list[idx] = {
        "name": req.name,
        "url": req.url,
        "api_key": req.api_key or existing.get("api_key", ""),
        "slug": instance_id,  # id is immutable across an edit
    }
    extras[service] = svc_list
    await _apply_instances(request, extras)
    inst = next(i for i in settings.instances if i.service == service and i.id == instance_id)
    return _serialize(inst)


@router.delete("/instances/{service}/{instance_id}")
async def delete_instance(service: str, instance_id: str, request: Request) -> dict:
    from ..config import settings, validate_library_bindings
    from ..libraries import slugify

    if service not in _ARR_SERVICES:
        raise HTTPException(422, detail=f"unknown service {service!r}")
    if instance_id.strip() == "":
        raise HTTPException(400, detail="the default instance cannot be removed")
    extras = _extras()
    svc_list = list(extras.get(service, []))
    new_list = [i for i in svc_list if (i.get("slug") or slugify(i.get("name", ""))) != instance_id]
    if len(new_list) == len(svc_list):
        raise HTTPException(404, detail=f"{service} instance {instance_id!r} not found")
    extras[service] = new_list
    await _apply_instances(request, extras)
    warnings = validate_library_bindings(settings.libraries, settings.instances)
    return {"removed": True, "binding_warnings": warnings}


async def _plex_section_for(request: Request, lib) -> dict:
    """Live-match the library's fs_root to a Plex section (displayed, not stored).
    Returns {name|None, matched: bool}. Never raises (Plex may be unconfigured)."""
    plex = getattr(request.app.state.integrations, "plex", None)
    try:
        if plex is not None and plex.is_configured():
            section = await plex._section_for_path(str(lib.fs_root))
            return {"name": section, "matched": bool(section)}
    except Exception:  # noqa: BLE001 - topology must render even if Plex errs
        log.debug("plex section match failed for %s", lib.slug, exc_info=True)
    return {"name": None, "matched": False}


@router.get("/topology")
async def topology(request: Request) -> dict:
    from ..config import settings, validate_library_bindings

    libs = []
    for lib in settings.libraries:
        plex = await _plex_section_for(request, lib)
        libs.append(
            {
                "slug": lib.slug,
                "name": lib.name or "(default)",
                "sonarr_id": lib.sonarr_id,
                "radarr_id": lib.radarr_id,
                "bazarr_id": lib.bazarr_id,
                "plex_section": plex["name"],
                "plex_matched": plex["matched"],
            }
        )
    return {
        "libraries": libs,
        "binding_warnings": validate_library_bindings(settings.libraries, settings.instances),
    }
