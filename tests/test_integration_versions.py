"""Settings status grid: Tautulli + Ollama report their versions.

Tautulli's liveness check now uses cmd=get_tautulli_info (which carries
tautulli_version) with a fail-soft fallback to the bare status cmd; Ollama
gains a best-effort GET /api/version.
"""

from __future__ import annotations

import httpx
import pytest


@pytest.mark.asyncio
async def test_tautulli_status_returns_version(subarr_env):
    from subarr.integrations.tautulli import TautulliClient

    c = TautulliClient.__new__(TautulliClient)

    async def fake_cmd(cmd, **params):
        assert cmd == "get_tautulli_info"
        return {"tautulli_version": "2.15.2"}

    c._cmd = fake_cmd
    out = await c.status()
    assert out == {"result": "success", "version": "2.15.2"}


@pytest.mark.asyncio
async def test_tautulli_status_falls_back_to_bare_status(subarr_env):
    from subarr.integrations import IntegrationError
    from subarr.integrations.tautulli import TautulliClient

    c = TautulliClient.__new__(TautulliClient)
    calls = []

    async def fake_cmd(cmd, **params):
        calls.append(cmd)
        if cmd == "get_tautulli_info":
            raise IntegrationError("tautulli get_tautulli_info: unknown cmd")
        return None

    c._cmd = fake_cmd
    out = await c.status()
    assert out == {"result": "success"}
    assert calls == ["get_tautulli_info", "status"]


@pytest.mark.asyncio
async def test_ollama_version_best_effort(subarr_env):
    from subarr.integrations.ollama import OllamaClient

    c = OllamaClient(base_url="http://ollama.test")
    c._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"version": "0.9.1"})),
        base_url="http://ollama.test",
    )
    assert await c.version() == "0.9.1"

    c._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500)),
        base_url="http://ollama.test",
    )
    assert await c.version() is None
