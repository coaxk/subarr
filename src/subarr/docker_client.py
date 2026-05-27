"""Thin wrapper around the docker SDK.

Subarr's docker.sock surface is intentionally narrow: tail logs from the
subgen container, restart the subgen container, snapshot recent logs to
extract progress lines. Nothing else. This keeps the docker.sock security
tradeoff (root-equivalent) honest — no user input ever flows into Docker
SDK calls.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import AsyncIterator

import docker
from docker.errors import NotFound

from .config import settings

log = logging.getLogger(__name__)

# subgen emits progress lines like:
#   INFO:root:[ Cette nuit-là - S01E02 - TBA WEBDL-72.. ]  78% | 2040/2610 s [06:43<01:52,  5.06s/s] | Jobs: 1 processing, 0 queued
# Filename in brackets is left-truncated to ~38 chars + ".." when long.
# Capture: filename_prefix, pct, current_sec, total_sec, elapsed, eta, speed.
_PROGRESS_RE = re.compile(
    r"\[\s*(?P<name>.+?)\s*\.{0,2}\s*\]\s+"
    r"(?P<pct>\d+)%\s+\|\s+"
    r"(?P<cur>[\d.]+)/(?P<tot>[\d.]+)\s+s\s+"
    r"\[(?P<elapsed>[\d:]+)<(?P<eta>[\d:?]+),\s+(?P<speed>[\d.]+)s/s\]"
)


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

    async def recent_progress(self, tail: int = 80) -> dict[str, dict]:
        """Snapshot recent subgen log lines and pull the latest progress
        update per file. Returns {filename_prefix → {pct, cur_s, tot_s,
        elapsed, eta, speed_s_per_s}}. Last write wins per filename — the
        most recent line for each file is what the GUI shows.

        Keyed by the truncated name subgen emits in the bracket; the
        queue-merge step in routers/queue.py matches it against
        os.path.basename of each processing-task path."""
        def _do() -> dict[str, dict]:
            client = self._get()
            try:
                container = client.containers.get(settings.subgen_container)
            except NotFound:
                raise DockerUnavailable(f"container {settings.subgen_container!r} not found")
            # docker logs(tail=N) returns the last N lines as bytes.
            raw = container.logs(tail=tail, stream=False, timestamps=False)
            if isinstance(raw, bytes):
                text = raw.decode("utf-8", errors="replace")
            else:
                text = str(raw)
            out: dict[str, dict] = {}
            for line in text.splitlines():
                m = _PROGRESS_RE.search(line)
                if not m:
                    continue
                d = m.groupdict()
                out[d["name"]] = {
                    "pct": int(d["pct"]),
                    "cur_s": float(d["cur"]),
                    "tot_s": float(d["tot"]),
                    "elapsed": d["elapsed"],
                    "eta": d["eta"],
                    "speed_s_per_s": float(d["speed"]),
                }
            return out

        try:
            return await asyncio.to_thread(_do)
        except DockerUnavailable:
            return {}
        except Exception as e:
            log.debug("recent_progress error (non-fatal): %s", e)
            return {}

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
