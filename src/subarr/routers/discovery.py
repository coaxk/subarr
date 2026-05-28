"""GET /api/discovery — Tier-2 docker introspection for the wizard.

The onboarding wizard hits this to pre-fill integration URLs. Returns
all detected *arr-stack services + their inferred URLs. Falls back
gracefully (empty list) when docker isn't reachable.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api")


@router.get("/discovery")
async def discovery_index(request: Request) -> dict[str, Any]:
    disc = getattr(request.app.state, "docker_discovery", None)
    if disc is None:
        return {
            "available": False,
            "reason": "docker discovery not configured (set SUBARR_DOCKER_PROXY_URL or mount /var/run/docker.sock)",
            "services": [],
        }
    reachable = await disc.reachable()
    if not reachable:
        return {
            "available": False,
            "reason": "docker daemon not reachable",
            "services": [],
        }
    services = await disc.discover()
    return {
        "available": True,
        "services": [s.to_dict() for s in services],
        "count_by_service": _summarise(services),
    }


@router.get("/discovery/{service}")
async def discovery_one(service: str, request: Request) -> dict[str, Any]:
    disc = getattr(request.app.state, "docker_discovery", None)
    if disc is None:
        return {"available": False, "service": service, "candidates": []}
    candidates = await disc.discover_for_service(service)
    return {
        "available": True,
        "service": service,
        "candidates": [c.to_dict() for c in candidates],
    }


def _summarise(services: list) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in services:
        out[s.service] = out.get(s.service, 0) + 1
    return out
