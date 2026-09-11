"""#536: the Logs page said "Can't reach Docker" when SUBGEN_CONTAINER named a
container that did not exist (`sugben`, #524). Every DockerUnavailable was
rendered as a socket problem. The error now carries a reason code, and the
SSE stream_error event says which one it was, plus the configured name.

Modules are reloaded per test in dependency order: other suites reload
`subarr.docker_client`, and a class imported at the top of this file would
then be a different object from the one the router raises and `safe_error`
type-checks (the handoff's module-reload trap)."""

from __future__ import annotations

import asyncio
import dataclasses
import importlib
import json

import pytest
from docker.errors import NotFound
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def mods(monkeypatch):
    dc = importlib.reload(importlib.import_module("subarr.docker_client"))
    importlib.reload(importlib.import_module("subarr.error_detail"))
    lr = importlib.reload(importlib.import_module("subarr.routers.logs"))
    return dc, lr


def _name(monkeypatch, dc, lr, name: str):
    monkeypatch.setattr(dc, "settings", dataclasses.replace(dc.settings, subgen_container=name))
    monkeypatch.setattr(lr, "settings", dataclasses.replace(lr.settings, subgen_container=name))


def _missing_ops(dc):
    class _Ops(dc.DockerOps):
        def _get(self):
            class _Containers:
                def get(_self, name):
                    raise NotFound(f"No such container: {name}")

            class _Client:
                containers = _Containers()

            return _Client()

    return _Ops()


def _no_socket_ops(dc):
    class _Ops(dc.DockerOps):
        def _get(self):
            raise dc.DockerUnavailable("docker.from_env() failed: no socket", reason="socket")

    return _Ops()


def test_default_reason_is_socket(mods):
    dc, _ = mods
    assert dc.DockerUnavailable("x").reason == "socket"


def test_missing_container_is_its_own_reason(mods, monkeypatch):
    dc, lr = mods
    _name(monkeypatch, dc, lr, "sugben")

    async def go():
        async for _ in _missing_ops(dc).stream_subgen_logs(tail=1):
            pass

    with pytest.raises(dc.DockerUnavailable) as ei:
        asyncio.run(go())
    assert ei.value.reason == "container_not_found"


def _first_error_event(lr, ops) -> dict:
    app = FastAPI()
    app.include_router(lr.router)
    app.state.docker = ops
    with TestClient(app) as c, c.stream("GET", "/api/logs/events") as r:
        for line in r.iter_lines():
            if line.startswith("data: "):
                return json.loads(line[len("data: ") :])
    raise AssertionError("no data event")


def test_stream_error_names_the_missing_container(mods, monkeypatch):
    dc, lr = mods
    _name(monkeypatch, dc, lr, "sugben")
    ev = _first_error_event(lr, _missing_ops(dc))
    assert ev["reason"] == "container_not_found"
    assert ev["container"] == "sugben"
    assert ev["detail"] == "the service is unavailable or misconfigured"


def test_stream_error_for_no_socket_keeps_the_socket_reason(mods, monkeypatch):
    dc, lr = mods
    _name(monkeypatch, dc, lr, "subgen")
    ev = _first_error_event(lr, _no_socket_ops(dc))
    assert ev["reason"] == "socket"
    assert ev["container"] == "subgen"
