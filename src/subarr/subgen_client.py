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

import json
import logging
import socket
import ssl
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


# #479: a closed vocabulary describing WHY a subgen probe failed.
#
# unreachable() used to be returned from two structurally different conditions
# with the difference discarded at the point it was known: a transport error,
# and a non-200 response meaning something IS listening but is not a healthy
# subgen. Telemetry then showed one bucket over 108 installs with no way to
# tell "nothing is there" from "the wrong thing is there".
#
# ⚠️ This value is TRANSMITTED. It carries no hostname, URL, port or exception
# text, only which of a fixed set of shapes the failure had.
_PROBE_CHAIN_MAX = 8


def classify_probe_failure(exc: Exception | None = None, *, status: int | None = None) -> str:
    """Bucket a probe failure. Pass `exc` for a transport error, `status` for a
    non-200 response.

    ⚠️ The chain is walked rather than the top-level class inspected, because
    DNS failure and connection-refused BOTH surface as httpx.ConnectError. The
    discriminator (socket.gaierror vs ConnectionRefusedError) sits underneath,
    and classifying on the outer type collapses exactly the two cases this
    exists to separate.

    ⚠️ The chain shapes here were measured on Linux, which is what runs in
    production. On Windows httpx does not surface socket.gaierror at all, so a
    classifier written against a Windows observation would have mislabelled
    every real install while passing its own tests.
    """
    if status is not None:
        return f"http_{status}" if 100 <= status <= 599 else "http_other"

    # Walk __cause__/__context__, guarding against a cycle: this runs in the
    # app lifespan and must not hang it.
    seen: set[int] = set()
    chain: list[BaseException] = []
    cur: BaseException | None = exc
    while cur is not None and len(chain) < _PROBE_CHAIN_MAX and id(cur) not in seen:
        seen.add(id(cur))
        chain.append(cur)
        cur = cur.__cause__ or cur.__context__

    for e in chain:
        if isinstance(e, socket.gaierror):
            return "dns"
        if isinstance(e, ConnectionRefusedError):
            return "refused"
        if isinstance(e, ssl.SSLError):
            return "tls"

    if isinstance(exc, httpx.ConnectTimeout):
        return "connect_timeout"
    if isinstance(exc, httpx.ReadTimeout):
        return "read_timeout"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"

    # Message is INSPECTED, never emitted: some SSL failures arrive as a bare
    # ConnectError with no ssl.SSLError in the chain.
    text = str(exc or "")
    if "SSL" in text or "certificate" in text.lower():
        return "tls"

    # Anything unrecognised still gets a bucket. Returning None would put it
    # back in the undifferentiated hole this replaces.
    return "transport"


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
    # v4.3 capability: POST /batch accepts audio_language_override query
    # param that bypasses SKIP_IF_AUDIO_LANGUAGES + seeds Whisper source.
    # Detected via the /queue response's capabilities.audio_language_override
    # flag (only the v4.3+ patch ships it). Vanilla and v4.2 → False.
    audio_language_override: bool = False
    # v4.4 capability: POST /queue/cancel?path=… accepts queue-cancel
    # requests. subarr's Monitor cancel buttons gate on this; without
    # it the buttons render disabled with an explainer.
    queue_cancel: bool = False
    # v4.5 capability: POST /detect_language_robust runs N-chunk Whisper
    # detection and returns per-chunk + aggregate (majority vote, min
    # probability across agreeing chunks). Layer 3 of subarr's audio-
    # ground-truth funnel gates on this — without it the funnel falls
    # back to single-chunk detect_language (less reliable).
    robust_language_detection: bool = False
    # v4.16 capability (#17): POST /detect_language_robust accepts ?track=N to
    # detect a SPECIFIC audio stream — lets the Review track-mismatch flow confirm
    # a non-default track's language.
    detect_language_track: bool = False
    # v4.17 capability (#6): POST /config?wait=false runs the model switch on a
    # background thread + returns 202, with progress in /queue.config_switch.
    # post_config() polls instead of holding a request past the 120s read timeout.
    async_config: bool = False
    # v4.8 capability: POST /batch accepts a per-request `kwargs` JSON query
    # param that overrides global + per-language SUBGEN_KWARGS for THIS scan
    # only. The tuning-lab / arena gates on this — it lets one config sweep
    # run variant-by-variant without a subgen restart per variant. Vanilla /
    # ≤v4.7 → False (they silently ignore the unknown query param anyway).
    per_request_kwargs: bool = False
    # v4.9 capability: POST /batch accepts a per-request `task` (transcribe|
    # translate) query param that overrides the global TRANSCRIBE_OR_TRANSLATE
    # for that batch only. The tuning-lab arena gates on this to drive a
    # source-transcribe AND candidate-translate through one path-based channel.
    per_request_task: bool = False
    # v4.10 capability: POST /asr accepts ?path= (read audio from a subgen-
    # visible path — no upload) + ?kwargs= (per-request override, folded into
    # the dedup hash) and returns the sub over HTTP. The tuning-lab arena gates
    # on this — it's the no-shared-scratch channel (vs /batch's disk output).
    asr_arena: bool = False
    # v4.11 capability: POST /asr accepts ?base=vanilla — skips the global
    # SUBGEN_KWARGS + per-language layers so a recipe runs against library
    # defaults only. The tuning-lab arena ALWAYS tests from vanilla when this is
    # present, so sweeps are a property of the recipe (not the operator's tuned
    # env) and are comparable across users (federated #124). Old subgen → False,
    # and subarr omits the param (the merge-stack behaviour is unchanged).
    asr_vanilla_base: bool = False
    # v4.12 capability: /asr returns the single-pass Whisper-detected source
    # language in the X-Detected-Language header. LOW-TRUST HINT ONLY — single-
    # chunk detection is unreliable on non-speech (verified: a French file
    # detects en/nynorsk on its intro/silence windows). The arena labels sweeps
    # via robust_language_detection (/detect_language_robust, multi-chunk
    # majority) instead; this header is kept only as a cheap opportunistic hint.
    asr_detected_language: bool = False
    # v4.13 capability (#79): subgen's RUNTIME IGNORE_FORCED_SUBTITLES value.
    # When True, subgen TRANSCRIBES files whose only English embedded sub is a
    # forced track (forced subs cover foreign dialogue only — not a full
    # transcript); when False (vanilla, or the knob off) subgen STILL SKIPS
    # them. NOTE this is the live on/off value, not merely "supported" —
    # subarr's coverage gates the forced-only-EN partial gap on it so users
    # only see an actionable gap the connected subgen will actually fill.
    ignore_forced_subtitles: bool = False
    # v4.22 capability (#458): when True subgen treats an image-based
    # (PGS/VobSub) target-language track as NOT coverage and transcribes the
    # file. When False -- which is subgen's DEFAULT, unlike the forced knob --
    # it still skips those files. Live on/off value, not merely "supported":
    # coverage gates the image-only partial gap on it so an un-fillable gap is
    # never presented as actionable.
    ignore_image_subtitles: bool = False
    # v4.23 capability (#458 follow-on): POST /batch accepts bypass_skip=true,
    # which skips EVERY skip heuristic for that one request. Needed for the
    # file class that has English audio AND only image-based English subs:
    # SKIP_IF_AUDIO_LANGUAGES refuses it and no other knob reaches it without
    # unskipping the whole library. Gate the UI action on this -- an older
    # subgen drops the unknown param and re-skips, which reads as a dead button.
    bypass_skip: bool = False
    # r9+ capability: POST /config runtime model/compute switching. Gate
    # post_config() calls on this — older images 404 the endpoint.
    runtime_config: bool = False
    # v4.3+ patch revision string ('v4.3', 'v4.4', ...) when published by
    # subgen. Lets subarr feature-gate behaviour without sniffing each
    # capability individually.
    subarr_subgen_patch_rev: str | None = None
    # #224: the GHCR release tag this image was built from (e.g.
    # 'v2026.05.3-r9'), baked at build time and published by subarr-subgen
    # r9+. THIS is what update checks compare against the repo's release
    # tags — the patch_rev scheme (v4.x) can never match a date-style tag.
    # None on older builds → the update checker won't claim an update.
    release_tag: str | None = None
    # r10 capability (#202 follow-up): subgen's live CONCURRENT_TRANSCRIPTIONS
    # value — the number of transcriptions its worker pool runs at once. subarr
    # uses it as the GPU-concurrency budget that both the feeder and the arena
    # gate on (see subgen_capacity). None on older subgen that doesn't publish
    # it → the gate stays disabled (dormant-safe).
    concurrent_transcriptions: int | None = None
    # v4.15 capability (#317 Slice B): subgen honours a per-REQUEST
    # ignore_forced flag on POST /batch (overrides the global
    # IGNORE_FORCED_SUBTITLES for one job). subarr gates its "transcribe a full
    # sub anyway" action on this — older subgen silently drops the unknown
    # query param and would re-skip the forced-only file. Distinct from
    # ignore_forced_subtitles above, which is subgen's GLOBAL runtime value.
    request_ignore_forced: bool = False
    # #479: WHY the probe failed, from a closed vocabulary (see
    # classify_probe_failure). None when reachable. Declared explicitly
    # because this dataclass is FROZEN: #458 shipped a gate that could never
    # open because it read an undeclared field via getattr with a default.
    probe_failure: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "reachable": self.reachable,
            "version": self.version,
            "has_queue": self.has_queue,
            "has_batch": self.has_batch,
            "is_subarr_subgen": self.is_subarr_subgen,
            "compat_mode": not self.is_subarr_subgen,
            "audio_language_override": self.audio_language_override,
            "queue_cancel": self.queue_cancel,
            "robust_language_detection": self.robust_language_detection,
            "detect_language_track": self.detect_language_track,
            "async_config": self.async_config,
            "per_request_kwargs": self.per_request_kwargs,
            "per_request_task": self.per_request_task,
            "asr_arena": self.asr_arena,
            "asr_vanilla_base": self.asr_vanilla_base,
            "asr_detected_language": self.asr_detected_language,
            "ignore_forced_subtitles": self.ignore_forced_subtitles,
            "ignore_image_subtitles": self.ignore_image_subtitles,
            "bypass_skip": self.bypass_skip,
            "runtime_config": self.runtime_config,
            "subarr_subgen_patch_rev": self.subarr_subgen_patch_rev,
            "release_tag": self.release_tag,
            "concurrent_transcriptions": self.concurrent_transcriptions,
            "request_ignore_forced": self.request_ignore_forced,
            "probe_failure": self.probe_failure,
        }

    @classmethod
    def unreachable(cls, reason: str | None = None) -> "SubgenCapabilities":
        """#479: `reason` records WHICH failure this was. Optional so the
        existing no-argument callers keep working unchanged."""
        return cls(
            probe_failure=reason,
            reachable=False,
            version=None,
            has_queue=False,
            has_batch=False,
            is_subarr_subgen=False,
            audio_language_override=False,
            queue_cancel=False,
            robust_language_detection=False,
            detect_language_track=False,
            async_config=False,
            per_request_kwargs=False,
            per_request_task=False,
            asr_arena=False,
            asr_vanilla_base=False,
            asr_detected_language=False,
            ignore_forced_subtitles=False,
            ignore_image_subtitles=False,
            bypass_skip=False,
            runtime_config=False,
            request_ignore_forced=False,
            subarr_subgen_patch_rev=None,
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

    async def queue_cancel(self, path: str) -> dict[str, Any]:
        """v4.4+: cancel a queued task by canonical path. Returns the
        full response body so callers can render the structured reason
        when cancellation isn't possible (already processing, not in
        queue). Callers should gate on capabilities.queue_cancel before
        calling — vanilla/older subgen will 404 with no useful payload."""
        try:
            r = await self._client.post("/queue/cancel", params={"path": path})
        except httpx.HTTPError as e:
            raise SubgenUnavailable(f"subgen /queue/cancel failed: {e}") from e
        try:
            return r.json()
        except ValueError:
            return {"cancelled": False, "_raw": r.text[:200], "status_code": r.status_code}

    async def detect_language_robust(
        self, path: str, chunks: int = 3, chunk_length_s: int = 30, track: int | None = None
    ) -> dict[str, Any]:
        """v4.5+: multi-chunk Whisper language detection. Returns the
        full per-chunk breakdown + aggregate vote so subarr's review UI
        can render evidence. Synchronous (~6-12s per call on a typical
        GPU); callers should background this — never block a user-facing
        request on it. Gate on capabilities.robust_language_detection
        before calling — vanilla subgen will 404."""
        # Wide read timeout: 3 chunks × ~10s + model load can hit 60s
        # on cold cache. The default 120s read timeout already covers it.
        params: dict[str, Any] = {"path": path, "chunks": chunks, "chunk_length_s": chunk_length_s}
        if track is not None:
            # v4.16 (#17): detect a SPECIFIC audio stream (0-based). Gate on
            # capabilities.detect_language_track — older subgen ignores the param.
            params["track"] = track
        try:
            r = await self._client.post("/detect_language_robust", params=params)
        except httpx.HTTPError as e:
            raise SubgenUnavailable(f"subgen /detect_language_robust failed: {e}") from e
        if r.status_code >= 500:
            raise SubgenUnavailable(
                f"subgen /detect_language_robust returned {r.status_code}: {r.text[:200]}"
            )
        try:
            return r.json()
        except ValueError as e:
            raise SubgenUnavailable(f"subgen /detect_language_robust returned non-json: {e}") from e

    async def status(self) -> dict[str, Any]:
        try:
            r = await self._client.get("/status")
        except httpx.HTTPError as e:
            raise SubgenUnavailable(f"subgen /status failed: {e}") from e
        if r.status_code != 200:
            raise SubgenUnavailable(f"subgen /status status {r.status_code}")
        # Fix D (#288): wrap JSONDecodeError so an HTML proxy page on a 200
        # raises SubgenUnavailable rather than a raw ValueError.
        try:
            return r.json()
        except ValueError as e:
            raise SubgenUnavailable(f"subgen /status returned non-json: {e}") from e

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
            reason = classify_probe_failure(e)
            log.warning("subgen capability probe: /status unreachable (%s): %s", reason, e)
            return SubgenCapabilities.unreachable(reason)
        if r.status_code != 200:
            # Something IS listening and answered. A completely different
            # diagnosis from "nothing is there", and previously indistinguishable.
            log.warning("subgen capability probe: /status returned %d", r.status_code)
            return SubgenCapabilities.unreachable(classify_probe_failure(status=r.status_code))

        # Extract version from the body. Patched + vanilla both use the
        # 'version' key but the shape may vary across upstream versions.
        version: str | None = None
        try:
            body = r.json()
            v = body.get("version")
            if isinstance(v, str) and v.startswith("Subgen "):
                # 'Subgen 2026.05.3, stable-ts ...' → grab the 2026.05.3
                rest = v[len("Subgen ") :].split(",", 1)[0].strip()
                if rest:
                    version = rest
        except Exception:
            pass  # version extraction is best-effort

        # 2. /queue — also surfaces v4.3+ patch_rev + capabilities block.
        has_queue = False
        audio_language_override = False
        queue_cancel = False
        robust_language_detection = False
        detect_language_track = False
        async_config = False
        per_request_kwargs = False
        per_request_task = False
        asr_arena = False
        asr_vanilla_base = False
        asr_detected_language = False
        ignore_forced_subtitles = False
        ignore_image_subtitles = False
        bypass_skip = False
        runtime_config = False
        concurrent_transcriptions: int | None = None
        request_ignore_forced = False
        patch_rev: str | None = None
        release_tag: str | None = None
        try:
            qr = await self._client.get("/queue")
            if 200 <= qr.status_code < 300:
                # Sanity: must have at least 'queued' or 'processing' keys
                try:
                    body = qr.json()
                    if isinstance(body, dict) and ("queued" in body or "processing" in body):
                        has_queue = True
                        # v4.3+ ships these alongside the standard queue body.
                        # Absent on v4.2 — degrade silently.
                        rev = body.get("subarr_subgen_patch_rev")
                        if isinstance(rev, str):
                            patch_rev = rev
                        # #224: r9+ publishes its baked image tag here.
                        tag = body.get("subarr_subgen_release_tag")
                        if isinstance(tag, str) and tag:
                            release_tag = tag
                        caps_block = body.get("capabilities") or {}
                        if isinstance(caps_block, dict):
                            audio_language_override = bool(caps_block.get("audio_language_override"))
                            queue_cancel = bool(caps_block.get("queue_cancel"))
                            robust_language_detection = bool(caps_block.get("robust_language_detection"))
                            detect_language_track = bool(caps_block.get("detect_language_track"))
                            async_config = bool(caps_block.get("async_config"))
                            per_request_kwargs = bool(caps_block.get("per_request_kwargs"))
                            per_request_task = bool(caps_block.get("per_request_task"))
                            asr_arena = bool(caps_block.get("asr_arena"))
                            asr_vanilla_base = bool(caps_block.get("asr_vanilla_base"))
                            asr_detected_language = bool(caps_block.get("asr_detected_language"))
                            ignore_forced_subtitles = bool(caps_block.get("ignore_forced_subtitles"))
                            ignore_image_subtitles = bool(caps_block.get("ignore_image_subtitles"))
                            bypass_skip = bool(caps_block.get("bypass_skip"))
                            runtime_config = bool(caps_block.get("runtime_config"))
                            request_ignore_forced = bool(caps_block.get("request_ignore_forced"))
                            _ct = caps_block.get("concurrent_transcriptions")
                            # bool is an int subclass — exclude it so a misconfig'd
                            # `true` doesn't silently parse as N=1 and serialize the feed.
                            concurrent_transcriptions = (
                                _ct
                                if isinstance(_ct, int) and not isinstance(_ct, bool) and _ct > 0
                                else None
                            )
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
            audio_language_override=audio_language_override,
            queue_cancel=queue_cancel,
            robust_language_detection=robust_language_detection,
            detect_language_track=detect_language_track,
            async_config=async_config,
            per_request_kwargs=per_request_kwargs,
            per_request_task=per_request_task,
            asr_arena=asr_arena,
            asr_vanilla_base=asr_vanilla_base,
            asr_detected_language=asr_detected_language,
            ignore_forced_subtitles=ignore_forced_subtitles,
            ignore_image_subtitles=ignore_image_subtitles,
            bypass_skip=bypass_skip,
            runtime_config=runtime_config,
            concurrent_transcriptions=concurrent_transcriptions,
            request_ignore_forced=request_ignore_forced,
            subarr_subgen_patch_rev=patch_rev,
            release_tag=release_tag,
        )
        log.info(
            "subgen capabilities: version=%s patch_rev=%s has_queue=%s has_batch=%s "
            "is_subarr_subgen=%s audio_lang_override=%s (compat_mode=%s)",
            caps.version,
            caps.subarr_subgen_patch_rev,
            caps.has_queue,
            caps.has_batch,
            caps.is_subarr_subgen,
            caps.audio_language_override,
            not caps.is_subarr_subgen,
        )
        return caps

    async def post_config(
        self,
        *,
        model: str | None = None,
        compute_type: str | None = None,
        async_config: bool = False,
        poll_timeout_s: float = 600.0,
    ) -> tuple[int, dict]:
        """POST /config — runtime model/compute switch (subarr-subgen r9+,
        gated on caps.runtime_config). Returns (status_code, body). subgen
        guarantees it ends on a working model (rollback contract); callers
        surface body['reason']/'current_model' on failure.

        async_config (v4.17+, caps.async_config): send ?wait=false so subgen runs
        the switch on a background thread + returns 202, then poll /queue.config_
        switch to completion — a switch to an uncached model (minutes-long
        download) no longer blows our read timeout. Returns the same (status,
        body) shape as the sync path."""
        params: dict[str, str] = {}
        if model:
            params["model"] = model
        if compute_type:
            params["compute_type"] = compute_type
        if async_config:
            params["wait"] = "false"
        try:
            r = await self._client.post("/config", params=params)
        except httpx.HTTPError as e:
            raise SubgenUnavailable(f"subgen /config failed: {e}") from e
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code == 202:
            return await self._poll_config_switch(poll_timeout_s)
        return r.status_code, body

    async def _poll_config_switch(self, timeout_s: float) -> tuple[int, dict]:
        """Poll GET /queue.config_switch until the async /config switch reaches a
        terminal state. Same (status, body) shape as the sync path: 200/ok,
        422/failed (with reason/rolled_back), or 504 on poll timeout."""
        import asyncio
        import time

        deadline = time.monotonic() + timeout_s
        while True:
            try:
                q = await self.queue()
            except SubgenUnavailable:
                q = {}  # transient — keep polling to the deadline
            cs = (q or {}).get("config_switch") or {}
            state = cs.get("state")
            if state == "done":
                return 200, {"ok": True, "model": cs.get("model"), "compute_type": cs.get("compute_type")}
            if state == "failed":
                return 422, {
                    "ok": False,
                    "reason": cs.get("reason"),
                    "detail": cs.get("detail"),
                    "rolled_back": cs.get("rolled_back"),
                    "current_model": cs.get("model"),
                    "current_compute_type": cs.get("compute_type"),
                }
            if time.monotonic() >= deadline:
                return 504, {
                    "ok": False,
                    "reason": "switch_timeout",
                    "detail": f"model switch still running after {int(timeout_s)}s",
                    "current_model": cs.get("model"),
                    "current_compute_type": cs.get("compute_type"),
                }
            await asyncio.sleep(2.0)

    async def batch(
        self,
        directory: str,
        reverse: bool = False,
        force_language: str | None = None,
        audio_language_override: str | None = None,
        kwargs: dict[str, Any] | None = None,
        task: str | None = None,
        ignore_forced: bool = False,
        bypass_skip: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        """POST /batch with subgen's V4.1 structured response.

        Returns (status_code, body). Caller distinguishes:
          - 200 + walked > 0 => scan dispatched (counts in body)
          - 404 + walked == 0 => path resolved to no files
          - other => caller decides; we don't raise on 404

        audio_language_override (v4.3+): user-verified ground-truth audio
        language (ISO 639-1 or LanguageCode-parseable). Bypasses the
        SKIP_IF_AUDIO_LANGUAGES skip-check and seeds Whisper's source-
        language hint. Caller should only set this when:
          * subgen advertised audio_language_override=True via /queue, AND
          * subarr has a user verification for this file in audio_lang_store
        Vanilla / v4.2 subgens will silently ignore the unknown query param.

        kwargs (v4.8+): per-request Whisper kwargs that override global +
        per-language SUBGEN_KWARGS for THIS scan only — serialized to a JSON
        query param. Used by the tuning-lab / arena to sweep configs without
        a subgen restart per variant. Empty/None → omitted entirely (keeps
        the request backward-compatible; ≤v4.7 silently ignore it). Gate on
        capabilities.per_request_kwargs before relying on the override.

        task (v4.9+): per-request 'transcribe' or 'translate' that overrides
        the global TRANSCRIBE_OR_TRANSLATE for THIS scan only. Lets the arena
        drive a source-transcribe and a candidate-translate over one channel.
        None → omitted (subgen keeps its global). Gate on
        capabilities.per_request_task before relying on it. Raises ValueError
        on any other value (fail loud rather than silently send junk).
        """
        if task is not None and task not in ("transcribe", "translate"):
            raise ValueError(f"task must be 'transcribe' or 'translate', got {task!r}")
        # Normalize 3-letter arr/ffprobe tags (ISO 639-2/B 'fre', /T 'fra') to
        # the 2-letter ISO 639-1 ('fr') subgen's Whisper layer wants, at the wire
        # boundary. subgen /batch parses 3-letter leniently, but normalizing here
        # makes it unambiguous and matches the strict /asr path. normalize_lang
        # passes unknown codes through unchanged, so nothing is dropped.
        from .langs import normalize_lang

        params: dict[str, Any] = {"directory": directory, "reverse": str(reverse).lower()}
        if force_language:
            params["forceLanguage"] = normalize_lang(force_language) or force_language
        if audio_language_override:
            params["audio_language_override"] = (
                normalize_lang(audio_language_override) or audio_language_override
            )
        if kwargs:
            params["kwargs"] = json.dumps(kwargs)
        if task:
            params["task"] = task
        if ignore_forced:
            # v4.15 (#317 Slice B): override subgen's forced-only skip for THIS
            # batch only — drives "transcribe a full sub anyway". Omitted when
            # False so ≤v4.14 subgen keeps its global behaviour (and silently
            # drops the unknown param). Gate on capabilities.request_ignore_forced.
            params["ignore_forced"] = "true"
        if bypass_skip:
            # v4.23 (#458 follow-on): bypass EVERY skip heuristic for this batch.
            # Omitted when False so the request stays byte-identical to what
            # callers send today; a pre-v4.23 subgen drops the unknown param and
            # re-skips the file. Gate on capabilities.bypass_skip.
            params["bypass_skip"] = "true"
        try:
            r = await self._client.post("/batch", params=params)
        except httpx.HTTPError as e:
            raise SubgenUnavailable(f"subgen /batch failed: {e}") from e
        try:
            body = r.json()
        except ValueError:
            body = {"_raw": r.text[:500]}
        return r.status_code, body

    async def asr(
        self,
        path: str | None = None,
        *,
        local_file: str | None = None,
        task: str = "transcribe",
        language: str | None = None,
        kwargs: dict[str, Any] | None = None,
        initial_prompt: str | None = None,
        base: str | None = None,
        return_language: bool = False,
        timeout_s: float = 7200.0,
    ):
        """v4.10+ arena channel: ASR that BLOCKS until done and returns the
        subtitle TEXT over HTTP. Two input modes:

          - `path`: a subgen-visible path (subgen reads it off the shared media
            mount — no upload). Good for whole files.
          - `local_file`: UPLOAD a local file (multipart). Used for the
            tuning-lab's short auto-sampled clip, so it works with no shared
            writable mount and the upload stays tiny.

        `task` is transcribe|translate; `kwargs` is a per-request Whisper
        override (folded into subgen's dedup hash so configs don't collide).
        Gate on capabilities.asr_arena before calling.

        Returns the raw subtitle text ('' if nothing produced). Raises
        SubgenUnavailable on transport failure or a structured error payload,
        ValueError on a bad task or missing input.
        """
        if task not in ("transcribe", "translate"):
            raise ValueError(f"task must be 'transcribe' or 'translate', got {task!r}")
        if not path and not local_file:
            raise ValueError("asr() needs either path= or local_file=")
        params: dict[str, Any] = {"task": task}
        if path:
            params["path"] = path
        if language:
            params["language"] = language
        if initial_prompt:
            params["initial_prompt"] = initial_prompt
        if kwargs:
            params["kwargs"] = json.dumps(kwargs)
        # v4.11: 'vanilla' makes subgen skip its global + per-language kwargs so
        # the recipe runs from a canonical base. Omitted entirely for old subgen
        # (the param is unknown there) — gate on capabilities.asr_vanilla_base.
        if base:
            params["base"] = base
        files = None
        if local_file:
            import os

            with open(local_file, "rb") as fh:
                content = fh.read()
            files = {"audio_file": (os.path.basename(local_file), content, "application/octet-stream")}
        # /asr blocks for the whole transcription — needs a wide read timeout.
        asr_timeout = httpx.Timeout(connect=3.0, read=timeout_s, write=10.0, pool=3.0)
        try:
            r = await self._client.post("/asr", params=params, files=files, timeout=asr_timeout)
        except httpx.HTTPError as e:
            raise SubgenUnavailable(f"subgen /asr failed: {e}") from e
        # Fix C (#288): a non-JSON error body (proxy 5xx, text/plain HTML) must
        # raise, not be silently returned as subtitle text. Check status before
        # the content-type branch so any 4xx/5xx raises regardless of body type.
        if r.status_code >= 400:
            raise SubgenUnavailable(f"subgen /asr {r.status_code}: {r.text[:200]}")
        # Success streams text/plain (the subtitle); errors return a JSON dict.
        if "application/json" in r.headers.get("content-type", ""):
            try:
                body = r.json()
            except ValueError:
                body = {"message": r.text[:200]}
            raise SubgenUnavailable(f"subgen /asr error: {body.get('message', body)}")
        # v4.12: detected source language in the X-Detected-Language header.
        # Opt-in tuple return keeps the plain-text return back-compatible.
        if return_language:
            return r.text, r.headers.get("x-detected-language")
        return r.text
