"""Mode C plumbing: runtime_config capability + POST /config call."""

from __future__ import annotations

import httpx
import pytest


def _client_with(handler):
    from subarr.subgen_client import SubgenClient

    c = SubgenClient(base_url="http://subgen.test")
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://subgen.test")
    return c


@pytest.mark.asyncio
async def test_probe_parses_runtime_config_capability(subarr_env):
    def handler(req):
        if req.url.path == "/status":
            return httpx.Response(200, json={"version": "2026.05.3"})
        if req.url.path == "/queue":
            return httpx.Response(
                200,
                json={
                    "queued": [],
                    "processing": [],
                    "subarr_subgen_patch_rev": "v4.14",
                    "subarr_subgen_release_tag": "v2026.05.3-r9",
                    "capabilities": {"runtime_config": True},
                },
            )
        return httpx.Response(404)

    caps = await _client_with(handler).probe_capabilities()
    assert caps.runtime_config is True
    assert caps.release_tag == "v2026.05.3-r9"


@pytest.mark.asyncio
async def test_probe_defaults_runtime_config_false_on_old_image(subarr_env):
    def handler(req):
        if req.url.path == "/status":
            return httpx.Response(200, json={"version": "2026.05.3"})
        if req.url.path == "/queue":
            return httpx.Response(
                200,
                json={"queued": [], "processing": [], "subarr_subgen_patch_rev": "v4.13", "capabilities": {}},
            )
        return httpx.Response(404)

    caps = await _client_with(handler).probe_capabilities()
    assert caps.runtime_config is False


@pytest.mark.asyncio
async def test_post_config_success_and_failure_shapes(subarr_env):
    def ok_handler(req):
        assert req.url.path == "/config"
        assert req.url.params["model"] == "large-v3"
        return httpx.Response(200, json={"ok": True, "model": "large-v3", "compute_type": "float16"})

    out = await _client_with(ok_handler).post_config(model="large-v3", compute_type="float16")
    assert out == (200, {"ok": True, "model": "large-v3", "compute_type": "float16"})

    def oom_handler(req):
        return httpx.Response(
            422,
            json={"ok": False, "reason": "oom", "current_model": "medium", "current_compute_type": "float16"},
        )

    status, body = await _client_with(oom_handler).post_config(model="large-v3")
    assert status == 422 and body["reason"] == "oom" and body["current_model"] == "medium"
