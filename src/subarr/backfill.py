"""#116 — throttled library backfill (selection/throttle CORE).

Borrowed from Sonarr_Backfiller: instead of dumping the whole subtitle-gap
backlog into subgen at once (and flooding the GPU), trickle it — keep subgen's
queue topped up to a target depth, adding a bounded batch each tick. On a big
library this is the difference between a 500-job stampede and steadily closing
gaps in the background.

This module is the PURE decision math — given the eligible gap backlog, the
current queue depth, and the config, decide what to enqueue NOW. It does no
I/O, holds no state, and couples to nothing, so it's fully unit-tested.

WIRING (the documented next step, NOT built here — needs care + live tests):
  - eligibility: pull gaps from the coverage cache, filtered to probe-VERIFIED
    gaps only (never raw Bazarr-wanted) and past the settle-window (#117);
    order them (oldest? Tautulli-popularity? user-pickable).
  - queue depth: read subgen's live queue length (SubgenClient.queue()).
  - tick: call select_backfill_batch() on the scheduler cadence, enqueue the
    returned gaps, surface progress ("backfilling: N gaps, M in flight").
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BackfillConfig:
    # Default OFF — backfill never runs unless the user opts in. (Even when on,
    # the caller must still only feed VERIFIED gaps; see module docstring.)
    enabled: bool = False
    # Hold subgen's queue at this depth — the throttle that prevents a stampede.
    target_queue_depth: int = 3
    # Max gaps to enqueue per tick when below target (smooths bursty draining).
    batch: int = 5


def select_backfill_batch(gaps, queue_depth: int, config: BackfillConfig) -> list:
    """Return the slice of ``gaps`` to enqueue right now, bounded so the queue
    never overfills past ``target_queue_depth`` and never grows by more than
    ``batch`` in one tick. Pure: ``gaps`` is the caller's pre-filtered, ordered
    eligible backlog; the return is its front slice."""
    if not config.enabled:
        return []
    headroom = config.target_queue_depth - queue_depth
    if headroom <= 0:
        return []
    n = min(headroom, config.batch, len(gaps))
    return list(gaps[:n])
