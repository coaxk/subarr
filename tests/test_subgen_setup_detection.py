"""Detection inputs for guided subgen setup (spec §2.1 tiers 1-3)."""

from __future__ import annotations

import asyncio


def test_gpu_fields_include_compute_cap(subarr_env):
    from subarr.routers.gpu import _GPU_FIELDS

    assert "compute_cap" in _GPU_FIELDS
    assert "driver_version" in _GPU_FIELDS


def test_docker_nvidia_runtime_available(subarr_env):
    from subarr.docker_client import DockerOps

    ops = DockerOps()

    class _FakeClient:
        def info(self):
            return {"Runtimes": {"runc": {}, "nvidia": {"path": "nvidia-container-runtime"}}}

    ops._client = _FakeClient()
    assert asyncio.run(ops.nvidia_runtime_available()) is True

    class _FakeNoNvidia:
        def info(self):
            return {"Runtimes": {"runc": {}}}

    ops._client = _FakeNoNvidia()
    assert asyncio.run(ops.nvidia_runtime_available()) is False


def test_docker_nvidia_runtime_unavailable_docker_down(subarr_env):
    from subarr.docker_client import DockerOps

    ops = DockerOps()

    class _Boom:
        def info(self):
            raise RuntimeError("socket gone")

    ops._client = _Boom()
    # Fail-soft: detection tier degrades, never raises into the wizard.
    assert asyncio.run(ops.nvidia_runtime_available()) is None


def test_subgen_current_config_reads_env_and_gpu(subarr_env):
    from subarr.docker_client import DockerOps

    ops = DockerOps()

    class _FakeContainer:
        name = "subgen"
        short_id = "abc123"
        attrs = {
            "State": {"Status": "running", "Running": True, "StartedAt": "x"},
            "Config": {
                "Image": "ghcr.io/coaxk/subarr-subgen:2026.05.3-r8",
                "Env": [
                    "WHISPER_MODEL=large-v3",
                    "TRANSCRIBE_DEVICE=cuda",
                    "COMPUTE_TYPE=float16",
                    "PATH=/usr/bin",
                ],
            },
            "HostConfig": {"DeviceRequests": [{"Driver": "nvidia", "Count": 1}]},
        }

    class _FakeContainers:
        def get(self, name):
            return _FakeContainer()

    class _FakeClient:
        containers = _FakeContainers()

    ops._client = _FakeClient()
    cfg = asyncio.run(ops.subgen_current_config())
    assert cfg["whisper_model"] == "large-v3"
    assert cfg["transcribe_device"] == "cuda"
    assert cfg["compute_type"] == "float16"
    assert cfg["has_gpu_reservation"] is True
    assert cfg["image"].endswith(":2026.05.3-r8")
