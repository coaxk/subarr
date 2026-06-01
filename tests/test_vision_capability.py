"""#232 — vision-pre-filter capability detection + graceful degrade.

Regression suite for the launch-day wrinkle: previously, when a user
had no vision-capable model installed (i.e. their OLLAMA_MODEL was
qwen2.5:7b or similar text-only), subarr would silently call the text
model with image data attached and get hallucinated descriptions back.
After #232, vision_describe() refuses the call cleanly, the Settings
panel shows "vision pre-filter inactive", and the wizard can offer a
one-click pull of the recommended vision model.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from subarr.integrations.ollama import (
    _VISION_FAMILIES,
    OllamaClient,
    OllamaError,
    _is_vision_capable,
)


# Family-prefix detection: must catch tagged variants without false positives.

@pytest.mark.parametrize(
    "name,expected",
    [
        ("qwen2.5vl:7b", True),
        ("qwen2.5vl:7b-q4_K_M", True),
        ("llama3.2-vision:11b", True),
        ("llava:13b", True),
        ("bakllava:latest", True),
        ("minicpm-v:8b", True),
        ("moondream:latest", True),
        # NOT vision-capable, must not match.
        ("qwen2.5:7b", False),
        ("llama3.2:1b", False),
        ("nomic-embed-text", False),
        ("phi3.5:3.8b", False),
        ("", False),
    ],
)
def test_family_match(name, expected):
    assert _is_vision_capable(name) is expected


# resolve_vision_model() honours explicit config when the model is installed.

@pytest.mark.asyncio
async def test_resolve_explicit_installed():
    c = OllamaClient(base_url="http://ollama:11434", model="qwen2.5:7b",
                     vision_model="qwen2.5vl:7b")
    c.installed_models = AsyncMock(return_value=["qwen2.5:7b", "qwen2.5vl:7b"])
    resolved = await c.resolve_vision_model()
    assert resolved == "qwen2.5vl:7b"


# When config points at a NOT-installed model, auto-pick first installed
# vision-capable. This is the "user changed OLLAMA_MODEL to text-only
# but never pulled the vision model" path.

@pytest.mark.asyncio
async def test_resolve_falls_back_to_installed_vision():
    c = OllamaClient(base_url="http://ollama:11434", model="qwen2.5:7b",
                     vision_model="qwen2.5vl:7b")
    c.installed_models = AsyncMock(return_value=["qwen2.5:7b", "llava:13b"])
    resolved = await c.resolve_vision_model()
    assert resolved == "llava:13b"


# The "auto" sentinel skips the explicit check and picks first-installed.

@pytest.mark.asyncio
async def test_resolve_auto_picks_first_vision():
    c = OllamaClient(base_url="http://ollama:11434", model="qwen2.5:7b",
                     vision_model="auto")
    c.installed_models = AsyncMock(return_value=["qwen2.5:7b", "moondream:latest"])
    resolved = await c.resolve_vision_model()
    assert resolved == "moondream:latest"


# Family preference order: when multiple vision models are installed,
# we pick the best-fit-first (qwen2.5vl > llava > moondream).

@pytest.mark.asyncio
async def test_resolve_prefers_qwen_over_llava():
    c = OllamaClient(base_url="http://ollama:11434", model="qwen2.5:7b",
                     vision_model="auto")
    c.installed_models = AsyncMock(
        return_value=["moondream:latest", "llava:13b", "qwen2.5vl:7b"]
    )
    resolved = await c.resolve_vision_model()
    assert resolved == "qwen2.5vl:7b"


# The CRITICAL regression: no vision model installed -> None, not
# silently fall through to the text model.

@pytest.mark.asyncio
async def test_resolve_none_when_no_vision_capable():
    c = OllamaClient(base_url="http://ollama:11434", model="qwen2.5:7b",
                     vision_model="qwen2.5vl:7b")
    c.installed_models = AsyncMock(return_value=["qwen2.5:7b", "phi3.5:3.8b"])
    resolved = await c.resolve_vision_model()
    assert resolved is None


# vision_describe must REFUSE when no vision model is installed,
# raising the actionable error. Previously this would silently invoke
# the text model and return hallucinations.

@pytest.mark.asyncio
async def test_vision_describe_refuses_without_vision_model():
    # Re-import inside the function: the subarr_env fixture in conftest
    # calls importlib.reload(subarr.integrations.ollama), which creates
    # a NEW OllamaError class. Without re-import we'd hold the pre-reload
    # class reference and `except OllamaError` would not match the
    # post-reload class the OllamaClient actually raises.
    from subarr.integrations.ollama import OllamaClient as _Client
    from subarr.integrations.ollama import OllamaError as _Err
    c = _Client(base_url="http://ollama:11434", model="qwen2.5:7b",
                vision_model="qwen2.5vl:7b")
    c.installed_models = AsyncMock(return_value=["qwen2.5:7b"])  # text only
    raised: _Err | None = None
    try:
        await c.vision_describe(image_b64="x", prompt="p")
    except _Err as e:
        raised = e
    assert raised is not None, "expected OllamaError, got no exception"
    assert "no vision-capable model" in str(raised), str(raised)


# The resolved-model cache invalidates after pull_model completes — so
# the user pulling a vision model is reflected without restart.

@pytest.mark.asyncio
async def test_reset_vision_cache_after_pull():
    c = OllamaClient(base_url="http://ollama:11434", model="qwen2.5:7b",
                     vision_model="qwen2.5vl:7b")
    c.installed_models = AsyncMock(return_value=["qwen2.5:7b"])
    assert await c.resolve_vision_model() is None
    # Simulate post-pull state: vision model now appears.
    c.installed_models = AsyncMock(return_value=["qwen2.5:7b", "qwen2.5vl:7b"])
    # Without reset, the cached "None" persists — that's the bug we guard.
    assert await c.resolve_vision_model() is None
    c.reset_vision_cache()
    assert await c.resolve_vision_model() == "qwen2.5vl:7b"


# Allowlist sanity: families list is non-empty and qwen2.5vl is first
# (the recommended default). If someone reorders this we want to know.

def test_families_default_preference():
    assert len(_VISION_FAMILIES) >= 5
    assert _VISION_FAMILIES[0] == "qwen2.5vl"
