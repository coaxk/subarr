"""#479: eight installs were walking real libraries against a dead subgen,
one for 66 days, and nothing on the dashboard said so with any persistence.

Root cause of the silence: SubgenWatchdog keeps the OLD capabilities when a
probe fails ("keeping old caps"), so app.state.subgen_caps.reachable stays
True and the integration tile reads ok for as long as the last successful
probe said so. The watchdog now records the outage itself: when it started,
how many consecutive probes have failed, and why."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from subarr.subgen_client import SubgenCapabilities
from subarr.subgen_watchdog import SubgenWatchdog


class _Subgen:
    """Scripted probe results, consumed in order; the last one repeats."""

    def __init__(self, results):
        self.results = list(results)

    async def probe_capabilities(self):
        if len(self.results) > 1:
            return self.results.pop(0)
        return self.results[0]


def _reachable():
    return SubgenCapabilities(
        reachable=True, version="2026.08.1", is_subarr_subgen=True, has_queue=True, has_batch=True
    )


def _watchdog(subgen, initial=None):
    box = {"caps": initial}
    wd = SubgenWatchdog(
        subgen=subgen,
        get_caps=lambda: box["caps"],
        set_caps=lambda c: box.__setitem__("caps", c),
    )
    return wd, box


def _probe(wd, n=1):
    async def go():
        for _ in range(n):
            await wd._probe_once()

    asyncio.run(go())


def test_no_outage_while_reachable():
    wd, _ = _watchdog(_Subgen([_reachable()]), initial=_reachable())
    _probe(wd, 3)
    assert wd.outage() is None


def test_a_single_failed_probe_is_a_blip_not_an_outage():
    wd, box = _watchdog(_Subgen([SubgenCapabilities.unreachable("refused")]), initial=_reachable())
    _probe(wd, 1)
    assert wd.outage() is None
    # and the stale caps are still what the app sees, as before
    assert box["caps"].reachable is True


def test_consecutive_failures_become_an_outage_with_start_time_and_cause():
    wd, _ = _watchdog(_Subgen([SubgenCapabilities.unreachable("refused")]), initial=_reachable())
    _probe(wd, 3)
    o = wd.outage()
    assert o is not None
    assert o["consecutive"] == 3
    assert o["cause"] == "refused"
    assert o["since"] > 0
    assert o["seconds"] >= 0


def test_outage_start_is_the_first_failure_not_the_latest(monkeypatch):
    import subarr.subgen_watchdog as mod

    clock = {"t": 1000.0}
    monkeypatch.setattr(mod.time, "time", lambda: clock["t"])
    wd, _ = _watchdog(_Subgen([SubgenCapabilities.unreachable("dns")]), initial=_reachable())
    _probe(wd, 1)
    clock["t"] = 1030.0
    _probe(wd, 1)
    clock["t"] = 1060.0
    _probe(wd, 1)
    o = wd.outage()
    assert o["since"] == 1000.0
    assert o["seconds"] == 60.0


def test_cause_follows_the_latest_failed_probe():
    wd, _ = _watchdog(
        _Subgen([SubgenCapabilities.unreachable("dns"), SubgenCapabilities.unreachable("refused")]),
        initial=_reachable(),
    )
    _probe(wd, 3)
    assert wd.outage()["cause"] == "refused"


def test_recovery_clears_the_outage():
    wd, box = _watchdog(
        _Subgen(
            [
                SubgenCapabilities.unreachable("refused"),
                SubgenCapabilities.unreachable("refused"),
                _reachable(),
            ]
        ),
        initial=_reachable(),
    )
    _probe(wd, 3)
    assert wd.outage() is None
    assert box["caps"].reachable is True


def test_outage_starts_from_boot_when_subgen_was_never_reachable():
    # An install whose subgen has never answered: the baseline caps are the
    # boot probe's unreachable result. That is an outage too, not "no baseline".
    boot = SubgenCapabilities.unreachable("dns")
    wd, _ = _watchdog(_Subgen([SubgenCapabilities.unreachable("dns")]), initial=boot)
    _probe(wd, 2)
    o = wd.outage()
    assert o is not None and o["cause"] == "dns"


def test_dashboard_block_reports_the_outage_with_a_hint():
    from subarr.routers.home import _subgen_outage_block

    wd, _ = _watchdog(_Subgen([SubgenCapabilities.unreachable("refused")]), initial=_reachable())
    _probe(wd, 3)
    state = SimpleNamespace(subgen_watchdog=wd)
    block = _subgen_outage_block(state)
    assert block["cause"] == "refused"
    assert "port" in block["hint"].lower()
    assert block["seconds"] >= 0
    assert _subgen_outage_block(SimpleNamespace()) is None


def test_subgen_tile_goes_red_on_an_outage_even_with_stale_reachable_caps():
    from subarr.routers.home import _subgen_tile

    wd, box = _watchdog(_Subgen([SubgenCapabilities.unreachable("refused")]), initial=_reachable())
    _probe(wd, 3)
    assert box["caps"].reachable is True, "precondition: the cached caps are stale-reachable"
    state = SimpleNamespace(subgen_caps=box["caps"], subgen_watchdog=wd)
    tile = _subgen_tile(state)
    assert tile["status"] == "error"
    assert tile["extra"].startswith("unreachable for")
    assert "refused" in tile["extra"]


def test_subgen_tile_is_ok_when_reachable():
    from subarr.routers.home import _subgen_tile

    wd, _ = _watchdog(_Subgen([_reachable()]), initial=_reachable())
    _probe(wd, 1)
    tile = _subgen_tile(SimpleNamespace(subgen_caps=_reachable(), subgen_watchdog=wd))
    assert tile["status"] == "ok"
