"""#317 Slice B — pending queue persists the per-job ignore_forced flag.

The feeder reconstructs jobs from SQLite (not the in-memory enqueue() return),
so ignore_forced MUST round-trip through the DB column added in migration 025.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from subarr.migrate import run_migrations
from subarr.pending_queue import PendingQueueStore


@pytest.fixture
def store(tmp_path: Path) -> PendingQueueStore:
    db = tmp_path / "subarr.db"
    run_migrations(db)
    return PendingQueueStore(db)


def test_ignore_forced_round_trips_through_db(store):
    job = store.enqueue("TV/forced-only.mkv", source="gaps", ignore_forced=True)
    assert job.ignore_forced is True
    # Re-read from the DB (the path the feeder actually uses).
    reloaded = store.get(job.id)
    assert reloaded is not None
    assert reloaded.ignore_forced is True
    assert reloaded.to_dict()["ignore_forced"] is True


def test_ignore_forced_defaults_false(store):
    job = store.enqueue("TV/normal-gap.mkv", source="gaps")
    assert job.ignore_forced is False
    assert store.get(job.id).ignore_forced is False
