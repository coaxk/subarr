"""The single definition of subarr's subgen GPU-concurrency invariant.

subgen runs at most N = CONCURRENT_TRANSCRIPTIONS transcriptions at once via its
worker pool, but the Tuning-Lab arena's /asr "direct task" path runs OUTSIDE that
pool (and is invisible to subgen's GET /queue). To keep total concurrent GPU work
within the user's real limit, BOTH producers subarr drives — the pending-queue
feeder and the arena — consult this before committing work.

N is read live from subgen (capabilities.concurrent_transcriptions); when it is
unknown (old subgen, or subgen unreachable) the gate is disabled so behaviour is
exactly as before — no regression, no false stalls.
"""

from __future__ import annotations


def subgen_capacity_free(*, processing_count: int, arena_in_flight: int, n: int | None) -> bool:
    """True when subarr may start one more GPU-bound transcription.

    ``processing_count`` is subgen's reported processing count (includes foreign
    Plex/Bazarr jobs). ``arena_in_flight`` is subarr's count of arena /asr
    transcriptions currently running. ``n`` is subgen's concurrency limit, or
    None when unknown — in which case the gate is open (disabled)."""
    if n is None:
        return True
    return (processing_count + arena_in_flight) < n
