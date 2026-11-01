"""Provider-neutral LLM plumbing.

#616: subarr can talk to a local Ollama daemon or to any OpenAI-compatible
endpoint (llama-server, Unsloth, OpenAI, OpenRouter). This module holds the
error type both clients raise and the host-based profile resolver, so neither
client module needs to import the other.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from .ollama import OllamaClient
    from .openai_compat import OpenAICompatClient

log = logging.getLogger(__name__)

# Which thinking-off / token-cap dialect the endpoint speaks.
Profile = Literal["local", "openai", "openrouter"]

_PROFILES = ("local", "openai", "openrouter")


class LLMError(RuntimeError):
    def __init__(self, *args: object, status: int | None = None) -> None:
        super().__init__(*args)
        self.status = status


def build_llm_client() -> OllamaClient | OpenAICompatClient:
    """#616: the one active LLM client, chosen by settings.llm_provider."""
    from ..config import settings
    from .ollama import OllamaClient
    from .openai_compat import OpenAICompatClient

    if settings.llm_provider == "openai":
        return OpenAICompatClient(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            vision=settings.openai_vision,
            server_type=settings.openai_server_type,
        )
    if settings.llm_provider != "ollama":
        log.warning("unknown llm_provider %r; using ollama", settings.llm_provider)
    return OllamaClient()


def resolve_profile(base_url: str, server_type: str | None) -> Profile:
    """#616: pick the request dialect for an OpenAI-compatible endpoint.

    An explicit server type wins over the host — proxies and gateways front
    OpenAI under another hostname. `auto` (or an unknown value) resolves from
    the URL host: api.openai.com -> openai, openrouter.ai and its subdomains
    -> openrouter, everything else (including an empty URL) -> local.
    """
    chosen = (server_type or "").strip().lower()
    if chosen in _PROFILES:
        return chosen  # type: ignore[return-value]
    host = urlsplit(base_url.strip()).hostname or ""
    if host == "api.openai.com":
        return "openai"
    if host == "openrouter.ai" or host.endswith(".openrouter.ai"):
        return "openrouter"
    return "local"
