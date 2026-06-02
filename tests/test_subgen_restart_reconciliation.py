"""subgen restart reconciliation must not cry "lost on restart" when nothing
was actually lost.

Two layers:
  - mark_orphaned_before must NOT orphan items still present in subgen's live
    queue (a connectivity blip leaves the queue intact; only a real restart
    empties it).
  - the watchdog must require sustained unreachability, not a single transient
    probe failure, before declaring a bounce.
"""

from __future__ import annotations

import time

import pytest


# ─── Layer 2: evidence-based orphaning ──────────────────────────────

def test_mark_orphaned_excludes_live_subgen_queue(tmp_path):
    from subarr.migrate import run_migrations
    from subarr.scan_store import ScanStore, PATH_STATUS_OK, PATH_STATUS_ORPHANED

    db = tmp_path / "s.db"
    run_migrations(db)
    s = ScanStore(db)
    scan = s.create(["TV/Keep/keep.mkv", "TV/Lose/lose.mkv"], reverse=False)
    for r in scan.results:
        r.status = PATH_STATUS_OK
    s.save(scan)

    # keep.mkv is still sitting in subgen's live queue → must be preserved.
    n = s.mark_orphaned_before(
        time.time() + 10, completed_paths=set(), live_basenames={"keep.mkv"}
    )
    got = s.get(scan.id)
    st = {r.path: r.status for r in got.results}
    assert st["TV/Lose/lose.mkv"] == PATH_STATUS_ORPHANED
    assert st["TV/Keep/keep.mkv"] == PATH_STATUS_OK  # still in subgen → not lost
    assert n == 1


# ─── Layer 1: sustained-unreachability before a bounce ──────────────

def _reachable():
    from subarr.subgen_client import SubgenCapabilities
    return SubgenCapabilities(
        reachable=True, version="2026.05.3", has_queue=True, has_batch=True,
        is_subarr_subgen=True, subarr_subgen_patch_rev="v4.7",
    )


def _unreachable():
    from subarr.subgen_client import SubgenCapabilities
    return SubgenCapabilities.unreachable()


class _SeqSubgen:
    def __init__(self, seq):
        self._seq = list(seq)
        self._i = 0

    async def probe_capabilities(self):
        r = self._seq[min(self._i, len(self._seq) - 1)]
        self._i += 1
        return r


def _watchdog(subgen, on_restart):
    from subarr.subgen_watchdog import SubgenWatchdog
    box = {"caps": _reachable()}  # reachable baseline
    return SubgenWatchdog(
        subgen_provider=lambda: subgen,
        get_caps=lambda: box["caps"],
        set_caps=lambda c: box.__setitem__("caps", c),
        on_restart=on_restart,
    )


@pytest.mark.asyncio
async def test_single_blip_does_not_fire_restart():
    fired = []

    async def on_restart(old, new, at):
        fired.append((old, new, at))

    w = _watchdog(_SeqSubgen([_unreachable(), _reachable()]), on_restart)
    await w._probe_once()  # one unreachable blip
    await w._probe_once()  # reachable again
    assert fired == [], "a single transient blip must not be treated as a restart"


@pytest.mark.asyncio
async def test_sustained_outage_fires_restart():
    fired = []

    async def on_restart(old, new, at):
        fired.append((old, new, at))

    w = _watchdog(_SeqSubgen([_unreachable(), _unreachable(), _reachable()]), on_restart)
    await w._probe_once()
    await w._probe_once()
    await w._probe_once()
    assert len(fired) == 1, "a sustained outage (>=2 probes) should count as a restart"
