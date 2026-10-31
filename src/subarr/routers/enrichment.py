"""LLM enrichment HTTP API.

- POST /api/enrichment/lang  → enrich a single (path, title) pair.
- POST /api/enrichment/lang/bulk → enrich a list, returning per-row results.
- GET  /api/enrichment/health → ollama up? which model?

Both POST endpoints honor a GPU-idle gate: if subgen reports
processing_count > 0 or idle == false, requests with ?gate=true return
429 with a 'subgen busy' reason instead of running. The frontend uses
gate=true by default; manual 'Enrich now' actions can override with
gate=false.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ..enrichment import enrich_one
from ..integrations.llm import LLMError
from ..integrations.openai_compat import LLMCoolingDown
from ..error_detail import safe_error
from ..subgen_client import SubgenUnavailable

router = APIRouter(prefix="/api/enrichment", tags=["enrichment"])
log = logging.getLogger(__name__)


class EnrichOneRequest(BaseModel):
    canonical_path: str
    title: str


class EnrichBulkRequest(BaseModel):
    items: list[EnrichOneRequest]


async def _gpu_busy(request: Request) -> str | None:
    """Returns a reason string if the GPU is currently busy (= don't run
    LLM), else None. Best-effort: if subgen is unreachable we return None
    (no gating) rather than blocking enrichment entirely."""
    try:
        q = await request.app.state.subgen.queue()
    except SubgenUnavailable:
        return None
    if (q.get("processing_count") or 0) > 0:
        proc = q.get("processing") or []
        path = proc[0].get("path") if proc else "?"
        return f"subgen busy: {q.get('processing_count')} transcribing ({path})"
    return None


@router.get("/health")
async def health(request: Request) -> dict:
    llm = request.app.state.llm
    if not llm.is_configured():
        return {
            "online": False,
            "configured": False,
            "model": llm.model,
            "provider": llm.provider,
        }
    if llm.provider == "openai":
        try:
            names = await llm.models()
        except LLMError as e:
            return {
                "online": False,
                "configured": True,
                "model": llm.model,
                "provider": llm.provider,
                "error": safe_error(e),
            }
        return {
            "online": True,
            "configured": True,
            "model": llm.model,
            "provider": llm.provider,
            "model_available": llm.model in names,
            "installed_models": names[:20],
            "cooling_down": llm.breaker.state == "open",
        }
    try:
        tags = await llm.tags()
    except LLMError as e:
        return {
            "online": False,
            "configured": True,
            "model": llm.model,
            "provider": llm.provider,
            "error": safe_error(e),
        }
    models = [m.get("name") for m in (tags.get("models") or [])]
    return {
        "online": True,
        "configured": True,
        "model": llm.model,
        "provider": llm.provider,
        "model_available": llm.model in models,
        "installed_models": models[:20],
    }


@router.post("/lang")
async def enrich_lang(req: EnrichOneRequest, request: Request, gate: bool = Query(True)) -> dict:
    llm = request.app.state.llm
    if not llm.is_configured():
        raise HTTPException(503, detail="llm not configured")
    if gate and llm.is_local_gpu:
        busy = await _gpu_busy(request)
        if busy:
            raise HTTPException(429, detail=busy)
    try:
        result = await enrich_one(
            canonical_path=req.canonical_path,
            title=req.title,
            ollama=llm,
            store=request.app.state.enrichment,
        )
    except LLMCoolingDown:
        raise HTTPException(503, detail="cooling down")
    except LLMError as e:
        raise HTTPException(503, detail=safe_error(e))
    return result.to_dict()


@router.post("/lang/bulk")
async def enrich_lang_bulk(
    req: EnrichBulkRequest,
    request: Request,
    gate: bool = Query(True),
    free_vram_after: bool = Query(
        True,
        description="v1.1-D: unload the Ollama model after "
        "the batch finishes, freeing VRAM for subgen. "
        "Set False if another enrichment batch is "
        "queued back-to-back.",
    ),
) -> dict:
    llm = request.app.state.llm
    if not llm.is_configured():
        raise HTTPException(503, detail="llm not configured")
    if gate and llm.is_local_gpu:
        busy = await _gpu_busy(request)
        if busy:
            raise HTTPException(429, detail=busy)
    results: list[dict] = []
    errors: list[dict] = []
    stopped: str | None = None
    # v1.1-D: keep the model resident for the whole batch (30m TTL is the
    # Ollama recommended "long-batch" value).
    for index, it in enumerate(req.items):
        try:
            r = await enrich_one(
                canonical_path=it.canonical_path,
                title=it.title,
                ollama=llm,
                store=request.app.state.enrichment,
                keep_alive="30m",
            )
            results.append(r.to_dict())
        except LLMCoolingDown:
            stopped = "cooling down"
            for remaining in req.items[index:]:
                errors.append({"canonical_path": remaining.canonical_path, "error": "cooling down"})
            break
        except LLMError as e:
            errors.append({"canonical_path": it.canonical_path, "error": safe_error(e)})
    model_unloaded = bool(free_vram_after and llm.supports_unload)
    if model_unloaded:
        await llm.unload()
    return {
        "results": results,
        "errors": errors,
        "stopped": stopped,
        "model_unloaded": model_unloaded,
    }


@router.post("/unload")
async def unload_model(request: Request) -> dict:
    """v1.1-D / polish #170: explicit "Clear VRAM" / "Unload model" button.
    Tells Ollama to evict the current model. No-op if Ollama isn't
    configured. Returns the GPU snapshot so the UI can show before/after."""
    llm = request.app.state.llm
    if not llm.supports_unload:
        return {
            "unloaded": False,
            "model": llm.model,
            "vram_before_mib": None,
            "vram_after_mib": None,
        }
    before = None
    after = None
    try:
        from .gpu import gpu_status

        before = await gpu_status()
    except Exception:
        pass
    await llm.unload()
    try:
        from .gpu import gpu_status

        after = await gpu_status()
    except Exception:
        pass
    return {
        "unloaded": True,
        "model": llm.model,
        "vram_before_mib": (before or {}).get("memory", {}).get("used_mib"),
        "vram_after_mib": (after or {}).get("memory", {}).get("used_mib"),
    }
