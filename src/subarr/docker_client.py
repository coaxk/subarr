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
import threading
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
    """Docker could not serve the request. `reason` says which kind:

    - ``socket``: the client could not be created or the daemon is unreachable
      (no socket mounted, wrong proxy URL, permission denied).
    - ``container_not_found``: the daemon answered, but nothing is named
      ``SUBGEN_CONTAINER``. #536: this used to render as a socket problem and
      sent an operator with a one-letter typo off to fix a mount that was fine.
    """

    def __init__(self, message: str, *, reason: str = "socket"):
        super().__init__(message)
        self.reason = reason


def _container_not_found() -> DockerUnavailable:
    return DockerUnavailable(
        f"container {settings.subgen_container!r} not found", reason="container_not_found"
    )


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
                raise DockerUnavailable(f"docker.from_env() failed: {e}", reason="socket") from e
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
                raise _container_not_found()
            container.restart(timeout=timeout)

        await asyncio.to_thread(_do)

    async def container_info(self) -> dict:
        def _do():
            client = self._get()
            try:
                c = client.containers.get(settings.subgen_container)
            except NotFound:
                raise _container_not_found()
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

    async def nvidia_runtime_available(self) -> bool | None:
        """Detection tier 2 (spec §2.1): does the Docker host have the
        nvidia runtime registered? True/False, or None when Docker itself
        is unreachable (fail-soft — the wizard degrades to manual entry,
        it never errors). Async: docker info() blocks on a dead socket."""

        def _do() -> bool:
            info = self._get().info()
            return "nvidia" in (info.get("Runtimes") or {})

        try:
            return await asyncio.to_thread(_do)
        except Exception as e:
            log.debug("nvidia_runtime_available failed (non-fatal): %s", e)
            return None

    async def subgen_current_config(self) -> dict:
        """Detection tier 3 (spec §2.1): subgen's CURRENT transcription env +
        GPU reservation, for the current-vs-recommended diff. Reads the same
        container attrs container_info() already trusts."""

        def _do() -> dict:
            client = self._get()
            try:
                c = client.containers.get(settings.subgen_container)
            except NotFound:
                raise _container_not_found()
            attrs = c.attrs
            env_list = ((attrs.get("Config") or {}).get("Env")) or []
            env = dict(e.split("=", 1) for e in env_list if "=" in e)
            device_requests = ((attrs.get("HostConfig") or {}).get("DeviceRequests")) or []
            has_gpu = any((d or {}).get("Driver") == "nvidia" for d in device_requests)
            return {
                "whisper_model": env.get("WHISPER_MODEL"),
                "transcribe_device": env.get("TRANSCRIBE_DEVICE"),
                "compute_type": env.get("COMPUTE_TYPE"),
                "has_gpu_reservation": has_gpu,
                "image": (attrs.get("Config") or {}).get("Image"),
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
                raise _container_not_found()
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
            raise _container_not_found()

        # #526: this used to leak. On consumer disconnect the pump thread stayed
        # blocked in the SDK generator until the CONTAINER exited, and every
        # further line scheduled a `q.put()` coroutine on the loop with nobody
        # consuming - the queue filled at 1024 and each subsequent put parked
        # as a Task forever. subgen's progress output produced thousands per
        # second: an event-loop stall and a "Task was destroyed but it is
        # pending" flood (#524). Three changes:
        #   1. the SDK stream is kept and CLOSED from the async side, which is
        #      the one thing that makes the blocked iterator return;
        #   2. lines are offered with put_nowait via call_soon_threadsafe and
        #      DROPPED on a full queue - a log tail may lose lines under
        #      backpressure, it may not park a task per line;
        #   3. `stop` is a thread Event checked before each offer.
        q: asyncio.Queue[str | None] = asyncio.Queue(maxsize=1024)
        loop = asyncio.get_running_loop()
        stop = threading.Event()
        stream = container.logs(stream=True, follow=True, tail=tail)

        def _offer(item: str | None) -> None:
            # Runs ON the loop thread. Never blocks, never creates a Task.
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                if item is None:
                    # The end-of-stream marker must land: make room for it.
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    q.put_nowait(None)
                # else: dropped under backpressure, by design

        def _post(item: str | None) -> None:
            try:
                loop.call_soon_threadsafe(_offer, item)
            except RuntimeError:
                pass  # loop closed: nobody is listening any more

        def _pump():
            # #537: a chunk is NOT a line. docker-py yields a TTY container's
            # log one BYTE per chunk (`_stream_raw_result`, chunk_size=1) and a
            # non-TTY container's one multiplexed frame per chunk, which may
            # carry several lines or half of one. Reassemble on newlines.
            buf = b""
            try:
                for chunk in stream:
                    if stop.is_set():
                        break
                    if not chunk:
                        continue
                    buf += chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8", errors="replace")
                    while True:
                        nl = buf.find(b"\n")
                        if nl < 0:
                            break
                        raw, buf = buf[:nl], buf[nl + 1 :]
                        if stop.is_set():
                            break
                        _post(raw.decode("utf-8", errors="replace").rstrip("\r"))
            except Exception as e:  # noqa: BLE001 - a closed socket surfaces as a variety of errors; all mean "done"
                log.debug("log pump exited: %s", e)
            finally:
                if buf and not stop.is_set():
                    _post(buf.decode("utf-8", errors="replace").rstrip("\r"))
                _post(None)

        worker = asyncio.create_task(asyncio.to_thread(_pump))
        try:
            while True:
                line = await q.get()
                if line is None:
                    break
                yield line
        finally:
            stop.set()
            try:
                stream.close()
            except Exception as e:  # noqa: BLE001 - best effort; the stop flag still ends the thread on its next chunk
                log.debug("log stream close failed: %s", e)
            # Let the worker unwind now that the stream is closed; never wait
            # on it longer than the socket teardown should take.
            try:
                await asyncio.wait_for(asyncio.shield(worker), timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            except Exception as e:  # noqa: BLE001
                log.debug("log pump worker ended with: %s", e)
