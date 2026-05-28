"""Thin async client for the patched subgen HTTP API.

One client per app lifetime; reused across requests so connection pooling kicks
in (queue polling will hit /queue every couple of seconds from the Monitor tab).

Capability detection: subarr can run against vanilla McCloudS/subgen too, not
just our patched ghcr.io/coaxk/subarr-subgen. Vanilla lacks GET /queue (our
v4.2 patch) and the structured POST /batch response (our v4.1 patch). On
startup we probe what's available and the rest of the app gates feature
surfaces on the result. See SubgenCapabilities below.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from .config import settings

log = logging.getLogger(__name__)

# 5s is generous for /queue / /status; /batch can take a while (it walks the folder
# tree synchronously inside subgen before returning the structured count). Pick
# something that won't time out on big folders but is short enough that a hung
# subgen surfaces quickly.
_DEFAULT_TIMEOUT = httpx.Timeout(connect=3.0, read=120.0, write=10.0, pool=3.0)


class SubgenUnavailable(RuntimeError):
    """Subgen container isn't reachable or returned an unparseable response."""


@dataclass(frozen=True)
class SubgenCapabilities:
    """What this subgen build supports. Computed once at app boot.

    Fields:
        reachable     — /status returned 200. If False, nothing else works.
        version       — subgen_version string (e.g. '2026.05.3') or None.
        has_queue     — GET /queue returns 200 with our v4.2 shape. False
                        on vanilla subgen. When False, subarr's header
                        counter hides, completion_watcher falls back to
                        provenance-table polling.
        has_batch     — POST /batch is assumed available; the structured
                        response (v4.1) is detected via /status version
                        string. When False (vanilla), scan submission UI
                        surfaces a clear "needs subarr-subgen" message
                        instead of broken behaviour.
        is_subarr_subgen — true iff this is our patched build (detected via
                        the subarr.subgen.* image labels OR the presence
                        of /queue + /batch contract surfaces).
    """
    reachable: bool
    version: str | None
    has_queue: bool
    has_batch: bool
    is_subarr_subgen: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "reachable": self.reachable,
            "version": self.version,
            "has_queue": self.has_queue,
            "has_batch": self.has_batch,
            "is_subarr_subgen": self.is_subarr_subgen,
            "compat_mode": not self.is_subarr_subgen,
        }

    @classmethod
    def unreachable(cls) -> "SubgenCapabilities":
        return cls(
            reachable=False, version=None,
            has_queue=False, has_batch=False, is_subarr_subgen=False,
        )


class SubgenClient:
    def __init__(self, base_url: str | None = None, timeout: httpx.Timeout | None = None):
        self._base_url = (base_url or settings.subgen_url).rstrip("/")
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout or _DEFAULT_TIMEOUT)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def queue(self) -> dict[str, Any]:
        try:
            r = await self._client.get("/queue")
        except httpx.HTTPError as e:
            raise SubgenUnavailable(f"subgen /queue failed: {e}") from e
        if r.status_code != 200:
            raise SubgenUnavailable(f"subgen /queue status {r.status_code}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError as e:
            raise SubgenUnavailable(f"subgen /queue returned non-json: {e}") from e

    async def status(self) -> dict[str, Any]:
        try:
            r = await self._client.get("/status")
        except httpx.HTTPError as e:
            raise SubgenUnavailable(f"subgen /status failed: {e}") from e
        if r.status_code != 200:
            raise SubgenUnavailable(f"subgen /status status {r.status_code}")
        return r.json()

    async def probe_capabilities(self) -> SubgenCapabilities:
        """One-shot startup probe: figure out what this subgen build can do.

        Order of probes (cheapest first, abort on first failure):
          1. GET /status — confirms reachable + extracts version string.
             If this fails, return SubgenCapabilities.unreachable().
          2. GET /queue — present iff our v4.2 patch landed. We accept
             any 2xx as "has it"; 404/405 means vanilla.
          3. /batch shape — inferred from version + queue presence. We
             intentionally DO NOT POST /batch as a probe (that would
             trigger a real scan). If /queue is present we assume /batch
             is our patched v4.1 too — we only ship them together.

        Called once from app lifespan. Result cached on app.state and
        surfaced via /api/integrations/health for the UI.
        """
        # 1. Status
        try:
            r = await self._client.get("/status")
        except httpx.HTTPError as e:
            log.warning("subgen capability probe: /status unreachable: %s", e)
            return SubgenCapabilities.unreachable()
        if r.status_code != 200:
            log.warning("subgen capability probe: /status returned %d", r.status_code)
            return SubgenCapabilities.unreachable()

        # Extract version from the body. Patched + vanilla both use the
        # 'version' key but the shape may vary across upstream versions.
        version: str | None = None
        try:
            body = r.json()
            v = body.get("version")
            if isinstance(v, str) and v.startswith("Subgen "):
                # 'Subgen 2026.05.3, stable-ts ...' → grab the 2026.05.3
                rest = v[len("Subgen "):].split(",", 1)[0].strip()
                if rest:
                    version = rest
        except Exception:
            pass  # version extraction is best-effort

        # 2. /queue
        has_queue = False
        try:
            qr = await self._client.get("/queue")
            if 200 <= qr.status_code < 300:
                # Sanity: must have at least 'queued' or 'processing' keys
                try:
                    body = qr.json()
                    if isinstance(body, dict) and ("queued" in body or "processing" in body):
                        has_queue = True
                except ValueError:
                    pass
        except httpx.HTTPError:
            pass  # /queue not present → vanilla

        # 3. /batch — paired with /queue in our patch stack, so we assume
        # they ship together. If has_queue, has_batch.
        has_batch = has_queue
        is_subarr_subgen = has_queue and has_batch

        caps = SubgenCapabilities(
            reachable=True,
            version=version,
            has_queue=has_queue,
            has_batch=has_batch,
            is_subarr_subgen=is_subarr_subgen,
        )
        log.info(
            "subgen capabilities: version=%s has_queue=%s has_batch=%s "
            "is_subarr_subgen=%s (compat_mode=%s)",
            caps.version, caps.has_queue, caps.has_batch,
            caps.is_subarr_subgen, not caps.is_subarr_subgen,
        )
        return caps

    async def batch(self, directory: str, reverse: bool = False, force_language: str | None = None) -> tuple[int, dict[str, Any]]:
        """POST /batch with subgen's V4.1 structured response.

        Returns (status_code, body). Caller distinguishes:
          - 200 + walked > 0 => scan dispatched (counts in body)
          - 404 + walked == 0 => path resolved to no files
          - other => caller decides; we don't raise on 404
        """
        params: dict[str, Any] = {"directory": directory, "reverse": str(reverse).lower()}
        if force_language:
            params["forceLanguage"] = force_language
        try:
            r = await self._client.post("/batch", params=params)
        except httpx.HTTPError as e:
            raise SubgenUnavailable(f"subgen /batch failed: {e}") from e
        try:
            body = r.json()
        except ValueError:
            body = {"_raw": r.text[:500]}
        return r.status_code, body
