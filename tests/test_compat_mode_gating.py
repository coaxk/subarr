"""Tests for compat-mode code-path gating (#86 phase 2).

Covers:
  - ScanRunner._check_can_scan raises CompatModeError when subgen
    lacks /batch (vanilla mccloud/subgen)
  - ScanRunner._check_can_scan passes when caps unknown (no provider)
  - ScanRunner._check_can_scan passes when caps say has_batch=True
  - ScanRunner._check_can_scan raises when subgen unreachable
  - CompletionWatcher.pass_pending no-ops when caps say has_queue=False,
    AND logs the warning exactly once across multiple ticks
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest


# Reimport inside each test that depends on class identity — conftest
# reloads subarr modules between tests, so a module-level
# `from subarr.scan_runner import CompatModeError` ends up holding the
# pre-reload class object while ScanRunner.raise's the post-reload
# class, breaking `pytest.raises(CompatModeError)`. Local imports get
# the current module's class on every test.
def _import_ScanRunner():
    from subarr.scan_runner import ScanRunner, CompatModeError

    return ScanRunner, CompatModeError


def _import_CompletionWatcher():
    from subarr.completion_watcher import CompletionWatcher

    return CompletionWatcher


# ─── Helpers ────────────────────────────────────────────────────────


@dataclass
class FakeCaps:
    reachable: bool = True
    has_queue: bool = True
    has_batch: bool = True
    is_subarr_subgen: bool = True
    version: str | None = "2026.05.3"


def _runner_with_caps(caps):
    ScanRunner, _ = _import_ScanRunner()
    return ScanRunner(
        subgen=MagicMock(),
        store=MagicMock(),
        caps_provider=lambda: caps,
    )


# ─── ScanRunner gate ────────────────────────────────────────────────


def test_scan_runner_passes_when_caps_unknown():
    """No caps probed yet → don't block scans (first-boot scenario)."""
    runner = _runner_with_caps(None)
    runner._check_can_scan()  # should not raise


def test_scan_runner_passes_when_has_batch_true():
    runner = _runner_with_caps(FakeCaps(has_batch=True))
    runner._check_can_scan()


def test_scan_runner_raises_when_has_batch_false():
    _, CompatModeError = _import_ScanRunner()
    runner = _runner_with_caps(FakeCaps(has_batch=False, is_subarr_subgen=False))
    with pytest.raises(CompatModeError) as exc:
        runner._check_can_scan()
    assert "Scan submission requires the /batch endpoint" in str(exc.value)
    assert "subarr-subgen" in str(exc.value)


def test_scan_runner_raises_when_subgen_unreachable():
    _, CompatModeError = _import_ScanRunner()
    runner = _runner_with_caps(FakeCaps(reachable=False))
    with pytest.raises(CompatModeError) as exc:
        runner._check_can_scan()
    assert "not reachable" in str(exc.value)


# ─── CompletionWatcher gate ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_watcher_skips_pending_pass_when_no_queue_endpoint(caplog):
    """When caps say has_queue=False, _pass_pending bails after logging
    the one-time warning, without calling subgen.queue()."""
    subgen = MagicMock()
    subgen.queue = AsyncMock(
        side_effect=AssertionError("watcher must NOT call subgen.queue() when has_queue=False")
    )
    provenance = MagicMock()
    provenance.pending.return_value = [MagicMock(canonical_path="TV/x.mkv", id=1)]

    CompletionWatcher = _import_CompletionWatcher()
    watcher = CompletionWatcher(
        subgen=subgen,
        bazarr=MagicMock(),
        provenance=provenance,
        caps_provider=lambda: FakeCaps(has_queue=False, is_subarr_subgen=False),
    )
    with caplog.at_level(logging.WARNING):
        await watcher._pass_pending()
    # Warning logged
    assert any("compat mode" in r.message.lower() for r in caplog.records)
    # subgen.queue was NOT called
    subgen.queue.assert_not_called()


@pytest.mark.asyncio
async def test_watcher_warns_only_once_across_ticks(caplog):
    """The one-time warning should fire on first compat-mode tick + then
    stay silent on subsequent ticks. Avoids log spam every 30s."""
    subgen = MagicMock()
    subgen.queue = AsyncMock()
    provenance = MagicMock()
    provenance.pending.return_value = [MagicMock(canonical_path="x", id=1)]

    CompletionWatcher = _import_CompletionWatcher()
    watcher = CompletionWatcher(
        subgen=subgen,
        bazarr=MagicMock(),
        provenance=provenance,
        caps_provider=lambda: FakeCaps(has_queue=False, is_subarr_subgen=False),
    )
    with caplog.at_level(logging.WARNING):
        await watcher._pass_pending()
        first_count = sum(1 for r in caplog.records if "compat mode" in r.message.lower())
        # Repeat 3 more times — should NOT log again
        await watcher._pass_pending()
        await watcher._pass_pending()
        await watcher._pass_pending()
        total_count = sum(1 for r in caplog.records if "compat mode" in r.message.lower())
    assert first_count == 1
    assert total_count == 1, f"expected 1 warning total, got {total_count}"


@pytest.mark.asyncio
async def test_watcher_uses_queue_when_has_queue_true():
    """has_queue=True → watcher hits subgen.queue() like normal."""
    subgen = MagicMock()
    subgen.queue = AsyncMock(return_value={"queued": [], "processing": []})
    provenance = MagicMock()
    provenance.pending.return_value = [MagicMock(canonical_path="TV/x.mkv", id=1, series_id=None)]
    # We need mark_completed to be a regular MagicMock since the watcher
    # calls it synchronously
    provenance.mark_completed = MagicMock()

    CompletionWatcher = _import_CompletionWatcher()
    watcher = CompletionWatcher(
        subgen=subgen,
        bazarr=MagicMock(),
        provenance=provenance,
        caps_provider=lambda: FakeCaps(has_queue=True),
    )
    await watcher._pass_pending()
    subgen.queue.assert_called_once()


@pytest.mark.asyncio
async def test_watcher_works_when_no_caps_provider():
    """Existing callers without a caps_provider (which defaults to
    returning None) should still work — the gate only fires when caps
    are KNOWN to lack /queue, not when caps are unknown."""
    subgen = MagicMock()
    subgen.queue = AsyncMock(return_value={"queued": [], "processing": []})
    provenance = MagicMock()
    provenance.pending.return_value = [MagicMock(canonical_path="TV/x.mkv", id=1, series_id=None)]
    provenance.mark_completed = MagicMock()

    CompletionWatcher = _import_CompletionWatcher()
    watcher = CompletionWatcher(
        subgen=subgen,
        bazarr=MagicMock(),
        provenance=provenance,
        # No caps_provider — default None
    )
    await watcher._pass_pending()
    # When caps are unknown, we proceed (don't block — first-boot safe)
    subgen.queue.assert_called_once()
