"""Provider-neutral LLM plumbing.

#616: subarr can talk to a local Ollama daemon or to any OpenAI-compatible
endpoint (llama-server, Unsloth, OpenAI, OpenRouter). This module holds the
error type both clients raise and the host-based profile resolver, so neither
client module needs to import the other.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

# Which thinking-off / token-cap dialect the endpoint speaks.
Profile = Literal["local", "openai", "openrouter"]

_PROFILES = ("local", "openai", "openrouter")


class LLMError(RuntimeError):
    pass


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
