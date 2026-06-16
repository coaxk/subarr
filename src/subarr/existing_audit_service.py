"""#216 Phase 3: the background service that runs the existing-subtitle audit.

Wires the pure runner (existing_audit.run_existing_audit) to real deps — read
the file, probe the sibling video for duration, score with the aftercare
engine, capture a sanitized preview cue, persist via the aftercare store —
behind a single-flight background task with pollable progress. A library walk
plus ffprobe-per-file can take minutes, so this never blocks a request: start
it, poll status.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict
from pathlib import Path

from .aftercare import evaluate_subtitle
from .existing_audit import (
    cue_preview,
    discover_external_srts,
    resolve_media_for_srt,
    run_existing_audit,
)
from .media_probe import ProbeError, probe

log = logging.getLogger(__name__)


def _read_srt(path: str) -> str:
    # Provider/scene subs are frequently latin-1 or mixed; replace undecodable
    # bytes rather than fail the whole file.
    return Path(path).read_text(encoding="utf-8", errors="replace")


async def _probe_duration(srt_path: str) -> float | None:
    """Media duration for the SRT's sibling video (enables the sync-overrun
    check). None when there's no sibling video or ffprobe can't read it —
    duration is optional to the scorer."""
    media = resolve_media_for_srt(srt_path)
    if media is None:
        return None
    try:
        return (await probe(media)).duration_s
    except ProbeError as e:
        log.debug("existing-audit: no duration for %s (%s)", srt_path, e)
        return None


class ExistingAuditService:
    """Single-flight runner for the library-wide existing-sub audit."""

    def __init__(self, *, aftercare_store, provenance, libraries, clock=time.time):
        self._store = aftercare_store
        self._provenance = provenance
        self._libraries = libraries  # iterable of Library (has .fs_root)
        self._clock = clock
        self._task: asyncio.Task | None = None
        self._state: dict = {
            "running": False,
            "done": 0,
            "total": 0,
            "summary": None,
            "started_at": None,
            "finished_at": None,
            "error": None,
        }

    def status(self) -> dict:
        return dict(self._state)

    def start(self) -> bool:
        """Kick off an audit. Returns False if one is already running
        (single-flight) — the caller surfaces 'already running' to the UI."""
        if self._state["running"]:
            return False
        self._state.update(
            running=True,
            done=0,
            total=0,
            summary=None,
            started_at=self._clock(),
            finished_at=None,
            error=None,
        )
        self._task = asyncio.create_task(self._run(), name="existing-audit")
        return True

    async def _run(self) -> None:
        try:
            roots = [lib.fs_root for lib in self._libraries]
            srts = discover_external_srts(roots)
            generated = self._provenance.completed_paths_since(0.0)  # all-time

            def on_progress(done: int, total: int) -> None:
                self._state["done"] = done
                self._state["total"] = total

            summary = await run_existing_audit(
                srts,
                generated_paths=generated,
                read_text=_read_srt,
                probe_duration=_probe_duration,
                evaluate=lambda text, dur: evaluate_subtitle(text, media_duration_s=dur),
                record=self._store.record,
                now=self._clock(),
                make_preview=cue_preview,
                on_progress=on_progress,
            )
            self._state["summary"] = asdict(summary)
        except Exception as e:  # noqa: BLE001 - a failed walk must update state, not crash the loop
            self._state["error"] = str(e)
            log.exception("existing-audit run failed")
        finally:
            self._state["running"] = False
            self._state["finished_at"] = self._clock()
