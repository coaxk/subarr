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
from ..integrations.ollama import OllamaError
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
    ollama = request.app.state.ollama
    if not ollama.is_configured():
        return {"online": False, "configured": False, "model": ollama.model}
    try:
        tags = await ollama.tags()
    except OllamaError as e:
        return {"online": False, "configured": True, "model": ollama.model, "error": safe_error(e)}
    models = [m.get("name") for m in (tags.get("models") or [])]
    return {
        "online": True,
        "configured": True,
        "model": ollama.model,
        "model_available": ollama.model in models,
        "installed_models": models[:20],
    }


@router.post("/lang")
async def enrich_lang(req: EnrichOneRequest, request: Request, gate: bool = Query(True)) -> dict:
    if gate:
        busy = await _gpu_busy(request)
        if busy:
            raise HTTPException(429, detail=busy)
    try:
        result = await enrich_one(
            canonical_path=req.canonical_path,
            title=req.title,
            ollama=request.app.state.ollama,
            store=request.app.state.enrichment,
        )
    except OllamaError as e:
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
    if gate:
        busy = await _gpu_busy(request)
        if busy:
            raise HTTPException(429, detail=busy)
    results: list[dict] = []
    errors: list[dict] = []
    ollama = request.app.state.ollama
    # v1.1-D: keep the model resident for the whole batch (30m TTL is the
    # Ollama recommended "long-batch" value).
    for it in req.items:
        try:
            r = await enrich_one(
                canonical_path=it.canonical_path,
                title=it.title,
                ollama=ollama,
                store=request.app.state.enrichment,
                keep_alive="30m",
            )
            results.append(r.to_dict())
        except OllamaError as e:
            errors.append({"canonical_path": it.canonical_path, "error": safe_error(e)})
    if free_vram_after:
        await ollama.unload()
    return {"results": results, "errors": errors, "model_unloaded": free_vram_after}


@router.post("/unload")
async def unload_model(request: Request) -> dict:
    """v1.1-D / polish #170: explicit "Clear VRAM" / "Unload model" button.
    Tells Ollama to evict the current model. No-op if Ollama isn't
    configured. Returns the GPU snapshot so the UI can show before/after."""
    ollama = request.app.state.ollama
    before = None
    after = None
    try:
        from .gpu import gpu_status

        before = await gpu_status()
    except Exception:
        pass
    await ollama.unload()
    try:
        from .gpu import gpu_status

        after = await gpu_status()
    except Exception:
        pass
    return {
        "unloaded": True,
        "model": ollama.model,
        "vram_before_mib": (before or {}).get("memory", {}).get("used_mib"),
        "vram_after_mib": (after or {}).get("memory", {}).get("used_mib"),
    }
