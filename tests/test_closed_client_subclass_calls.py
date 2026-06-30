"""#392: subclass methods that call self._client.* directly must degrade the
closed-client RuntimeError (raised when a live _rebuild_runtime_clients aclose()s
the client mid-request) to the integration's own error type — NOT bubble a raw
RuntimeError as a 500. Genuine (non-closed) RuntimeErrors must still propagate.

We monkeypatch the client method to raise the exact errors rather than really
closing the client: deterministic and isolation-proof (no real event-loop/pool
state to leak across tests).

NOTE: the integration classes are imported INSIDE each test. The conftest
subarr_env fixture calls importlib.reload(subarr.integrations.*), minting NEW
class objects per test — a pre-reload top-level import would make
`pytest.raises(OllamaError)` miss the post-reload class the client raises.
(Same gotcha handled the same way in test_vision_capability.py.)"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

# The exact phrase httpx raises when a request is sent on an aclose()d client.
_CLOSED = "Cannot send a request, as the client has been closed."


def _async_raise(exc):
    async def _boom(*a, **k):
        raise exc

    return _boom


def _sync_raise(exc):
    def _boom(*a, **k):
        raise exc

    return _boom


# ── Plex._request (central GET path) ──────────────────────────────────────────


async def test_plex_request_closed_client_degrades_to_integration_error():
    from subarr.integrations import IntegrationError
    from subarr.integrations.plex import PlexClient

    c = PlexClient(base_url="http://plex:32400", token="x")
    c._client.get = _async_raise(RuntimeError(_CLOSED))
    with pytest.raises(IntegrationError):
        await c._request("/status/sessions", None)


async def test_plex_request_unexpected_runtime_error_still_propagates():
    from subarr.integrations import IntegrationError
    from subarr.integrations.plex import PlexClient

    c = PlexClient(base_url="http://plex:32400", token="x")
    c._client.get = _async_raise(RuntimeError("genuinely broken"))
    with pytest.raises(RuntimeError) as ei:
        await c._request("/x", None)
    assert "genuinely broken" in str(ei.value)
    assert not isinstance(ei.value, IntegrationError)


# ── Ollama direct-client methods ──────────────────────────────────────────────


async def test_ollama_tags_closed_client_degrades_to_ollama_error():
    from subarr.integrations.ollama import OllamaClient, OllamaError

    c = OllamaClient(base_url="http://ollama:11434", model="m")
    c._client.get = _async_raise(RuntimeError(_CLOSED))
    with pytest.raises(OllamaError):
        await c.tags()


async def test_ollama_generate_closed_client_degrades_to_ollama_error():
    from subarr.integrations.ollama import OllamaClient, OllamaError

    c = OllamaClient(base_url="http://ollama:11434", model="m")
    c._client.post = _async_raise(RuntimeError(_CLOSED))
    with pytest.raises(OllamaError):
        await c.generate("hi")


async def test_ollama_vision_describe_closed_client_degrades_to_ollama_error():
    from subarr.integrations.ollama import OllamaClient, OllamaError

    c = OllamaClient(base_url="http://ollama:11434", model="m")
    c._client.post = _async_raise(RuntimeError(_CLOSED))
    # pass model + image_b64 so it reaches the self._client.post (skips
    # resolve_vision_model + the separate image-fetch client).
    with pytest.raises(OllamaError):
        await c.vision_describe(image_b64="Zm9v", prompt="describe", model="llava")


async def test_ollama_version_closed_client_returns_none():
    # version() is best-effort (returns None on any failure) — a closed client
    # must fall into that None path, not raise.
    from subarr.integrations.ollama import OllamaClient

    c = OllamaClient(base_url="http://ollama:11434", model="m")
    c._client.get = _async_raise(RuntimeError(_CLOSED))
    assert await c.version() is None


async def test_ollama_pull_model_closed_client_degrades_to_ollama_error():
    from subarr.integrations.ollama import OllamaClient, OllamaError

    c = OllamaClient(base_url="http://ollama:11434", model="m")
    # pull_model opens `async with self._client.stream(...)`; the stream() call
    # itself raises the closed-client error before the context is entered.
    c._client.stream = _sync_raise(RuntimeError(_CLOSED))
    with pytest.raises(OllamaError):
        async for _ in c.pull_model("m"):
            pass


async def test_ollama_generate_unexpected_runtime_error_propagates_unwrapped():
    # A genuine RuntimeError (e.g. "Event loop is closed") must propagate as-is,
    # NOT be mistranslated to OllamaError (OllamaError IS a RuntimeError, so the
    # _is_client_closed phrase match is what keeps them distinct).
    from subarr.integrations.ollama import OllamaClient, OllamaError

    c = OllamaClient(base_url="http://ollama:11434", model="m")
    c._client.post = _async_raise(RuntimeError("Event loop is closed"))
    with pytest.raises(RuntimeError) as ei:
        await c.generate("hi")
    assert "Event loop is closed" in str(ei.value)
    assert not isinstance(ei.value, OllamaError)
