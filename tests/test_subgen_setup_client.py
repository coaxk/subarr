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


@pytest.mark.asyncio
async def test_post_config_async_polls_to_completion(subarr_env):
    """#6: async_config=True sends ?wait=false, gets 202, then polls
    /queue.config_switch to a terminal 'done' state and returns the sync shape."""

    def handler(req):
        if req.url.path == "/config":
            assert req.url.params.get("wait") == "false"
            return httpx.Response(202, json={"status": "switching", "model": "large-v3"})
        if req.url.path == "/queue":
            return httpx.Response(
                200,
                json={
                    "queued": [],
                    "processing": [],
                    "config_switch": {"state": "done", "model": "large-v3", "compute_type": "float16"},
                },
            )
        if req.url.path == "/status":
            return httpx.Response(200, json={"version": "2026.06.4"})
        return httpx.Response(404)

    status, body = await _client_with(handler).post_config(
        model="large-v3", compute_type="float16", async_config=True, poll_timeout_s=10.0
    )
    assert status == 200
    assert body == {"ok": True, "model": "large-v3", "compute_type": "float16"}


@pytest.mark.asyncio
async def test_post_config_async_surfaces_failed_switch(subarr_env):
    """#6: a failed async switch (config_switch.state == 'failed') comes back as
    the sync 422 shape with reason + rolled_back."""

    def handler(req):
        if req.url.path == "/config":
            return httpx.Response(202, json={"status": "switching", "model": "large-v3"})
        if req.url.path == "/queue":
            return httpx.Response(
                200,
                json={
                    "queued": [],
                    "processing": [],
                    "config_switch": {
                        "state": "failed",
                        "reason": "oom",
                        "rolled_back": True,
                        "model": "medium",
                        "compute_type": "float16",
                    },
                },
            )
        if req.url.path == "/status":
            return httpx.Response(200, json={"version": "2026.06.4"})
        return httpx.Response(404)

    status, body = await _client_with(handler).post_config(
        model="large-v3", async_config=True, poll_timeout_s=10.0
    )
    assert status == 422
    assert body["reason"] == "oom" and body["rolled_back"] is True and body["current_model"] == "medium"
