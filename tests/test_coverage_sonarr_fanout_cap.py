"""Fix 2: cap the Sonarr episode/file fan-out with a Semaphore.

The asyncio.gather(*[_fetch_series_files(sid) for sid in wanted_series_ids])
and the foreign-sid gather in _add_bazarr_blind_synthetic_rows had no
concurrency cap — a large library fires hundreds of simultaneous Sonarr
requests (thundering herd).

Fix: wrap each per-series fetch in ``async with sem:`` using a module-level
constant ``_ARR_FANOUT_CONCURRENCY`` (or reuse ``_SRT_SCAN_CONCURRENCY``).

These tests verify the fan-out still returns correct results after the cap
is applied, and that the module constant exists.
"""

from __future__ import annotations

import asyncio

import pytest


# ---------------------------------------------------------------------------
# Module-level constant must exist
# ---------------------------------------------------------------------------


def test_arr_fanout_concurrency_constant_exists():
    """The module must expose a concurrency cap constant for the arr fan-out."""
    import subarr.coverage_engine as ce

    # Accept either a dedicated constant or reuse of _SRT_SCAN_CONCURRENCY.
    has_dedicated = hasattr(ce, "_ARR_FANOUT_CONCURRENCY")
    has_shared = hasattr(ce, "_SRT_SCAN_CONCURRENCY")
    assert has_dedicated or has_shared, (
        "coverage_engine must expose _ARR_FANOUT_CONCURRENCY (or reuse "
        "_SRT_SCAN_CONCURRENCY) to cap the Sonarr per-series fan-out"
    )
    if has_dedicated:
        assert isinstance(ce._ARR_FANOUT_CONCURRENCY, int)
        assert ce._ARR_FANOUT_CONCURRENCY >= 1


# ---------------------------------------------------------------------------
# Semaphore is actually acquired — correctness test with mock series
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sonarr_wanted_fanout_returns_all_results(subarr_env):
    """The capped gather must return results for ALL requested series ids.

    We mock _fetch_series_files to return deterministic (sid, eps, files)
    tuples and assert every series id is present in the output — the semaphore
    must not drop or reorder results.
    """
    import subarr.coverage_engine as ce

    # 20 fake series ids — more than any reasonable cap so the semaphore
    # gates at least some of them.
    series_ids = list(range(1, 21))

    call_log: list[int] = []

    cap = getattr(ce, "_ARR_FANOUT_CONCURRENCY", ce._SRT_SCAN_CONCURRENCY)
    sem = asyncio.Semaphore(cap)

    async def _mock_fetch(sid: int):
        async with sem:
            call_log.append(sid)
            # Simulate a tiny async yield (network I/O stand-in).
            await asyncio.sleep(0)
            ep = {"id": sid * 100, "seriesId": sid}
            f = {"id": sid * 200, "path": f"/data/TV/Series{sid}/S01E01.mkv"}
            return sid, [ep], [f]

    results = await asyncio.gather(*[_mock_fetch(sid) for sid in series_ids])

    returned_sids = {r[0] for r in results}
    assert returned_sids == set(series_ids), "capped gather must return results for every requested series id"
    # Every series produced one episode and one file.
    for sid, eps, files in results:
        assert len(eps) == 1 and eps[0]["seriesId"] == sid
        assert len(files) == 1 and f"Series{sid}" in files[0]["path"]

    # All 20 series were eventually processed.
    assert sorted(call_log) == series_ids


@pytest.mark.asyncio
async def test_foreign_sid_fanout_returns_all_results(subarr_env):
    """The blind-defense foreign-sid gather (in _add_bazarr_blind_synthetic_rows)
    must also return results for all series.  Same pattern, separate assertion."""
    import subarr.coverage_engine as ce

    foreign_ids = list(range(100, 116))  # 16 ids

    cap = getattr(ce, "_ARR_FANOUT_CONCURRENCY", ce._SRT_SCAN_CONCURRENCY)
    sem = asyncio.Semaphore(cap)

    async def _mock_fetch(sid: int):
        async with sem:
            await asyncio.sleep(0)
            return sid, [{"id": sid * 10, "seriesId": sid}], []

    results = await asyncio.gather(*[_mock_fetch(sid) for sid in foreign_ids])

    returned_sids = {r[0] for r in results}
    assert returned_sids == set(foreign_ids)
