"""Thin wrapper around the docker SDK.

Subarr's docker.sock surface is intentionally narrow: tail logs from the
subgen container, restart the subgen container. Nothing else. This keeps the
docker.sock security tradeoff (root-equivalent) honest — no user input ever
flows into Docker SDK calls.
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

import docker
from docker.errors import NotFound

from .config import settings

log = logging.getLogger(__name__)


class DockerUnavailable(RuntimeError):
    pass


class DockerOps:
    """Lazy docker.from_env() — let lifespan stay sync and surface failures
    on first use rather than on app boot (boot must succeed even if docker
    is down so /api/health still returns)."""

    def __init__(self):
        self._client = None

    def _get(self):
        if self._client is None:
            try:
                self._client = docker.from_env()
            except Exception as e:
                raise DockerUnavailable(f"docker.from_env() failed: {e}") from e
        return self._client

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    async def restart_subgen(self, timeout: int = 30) -> None:
        def _do():
            client = self._get()
            try:
                container = client.containers.get(settings.subgen_container)
            except NotFound:
                raise DockerUnavailable(f"container {settings.subgen_container!r} not found")
            container.restart(timeout=timeout)

        await asyncio.to_thread(_do)

    async def container_info(self) -> dict:
        def _do():
            client = self._get()
            try:
                c = client.containers.get(settings.subgen_container)
            except NotFound:
                raise DockerUnavailable(f"container {settings.subgen_container!r} not found")
            attrs = c.attrs
            state = attrs.get("State", {}) or {}
            return {
                "name": c.name,
                "status": state.get("Status"),
                "running": bool(state.get("Running")),
                "started_at": state.get("StartedAt"),
                "image": (attrs.get("Config") or {}).get("Image"),
                "id_short": c.short_id,
            }

        return await asyncio.to_thread(_do)

    async def stream_subgen_logs(self, tail: int = 200) -> AsyncIterator[str]:
        """Yields log lines from subgen, decoded UTF-8 (errors replaced).

        Backfills `tail` lines then follows. Runs the blocking docker SDK
        generator on a worker thread; each line is passed through an
        asyncio.Queue so callers can use plain `async for`."""
        client = self._get()
        try:
            container = client.containers.get(settings.subgen_container)
        except NotFound:
            raise DockerUnavailable(f"container {settings.subgen_container!r} not found")

        q: asyncio.Queue[str | None] = asyncio.Queue(maxsize=1024)
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()

        def _pump():
            try:
                for chunk in container.logs(stream=True, follow=True, tail=tail):
                    if stop.is_set():
                        break
                    if not chunk:
                        continue
                    if isinstance(chunk, bytes):
                        line = chunk.decode("utf-8", errors="replace").rstrip("\r\n")
                    else:
                        line = str(chunk).rstrip("\r\n")
                    asyncio.run_coroutine_threadsafe(q.put(line), loop)
            except Exception as e:
                log.debug("log pump exited: %s", e)
            finally:
                asyncio.run_coroutine_threadsafe(q.put(None), loop)

        thread = asyncio.create_task(asyncio.to_thread(_pump))
        try:
            while True:
                line = await q.get()
                if line is None:
                    break
                yield line
        finally:
            stop.set()
            # The blocking generator inside docker SDK will keep running until
            # the connection closes from its end; we can't interrupt it cleanly.
            # The thread will finish on its own when the container stream ends
            # or the underlying socket is broken. Mark done; don't await.
            thread.add_done_callback(lambda _: None)
