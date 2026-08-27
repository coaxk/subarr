"""#448: Aftercare showed the first page forever.

The endpoint already accepted limit/offset, but reported `count` = len(items),
i.e. the size of the page it had just returned. With no total, the UI could not
know more rows existed, and so it never sent an offset. These pin the total.
"""

from __future__ import annotations

import pytest

from subarr.aftercare import AftercareEvaluation
from subarr.aftercare_store import AfterCareStore
from subarr.migrate import run_migrations


@pytest.fixture()
def store(tmp_path):
    db = tmp_path / "a.db"
    run_migrations(db)
    return AfterCareStore(db)


def _add(store, i: int, *, flagged: bool, source: str = "existing_audit"):
    store.record(
        canonical_path=f"TV/Show/Season 1/ep{i:04d}.mkv",
        completed_at=1000.0 + i,
        evaluation=AftercareEvaluation(
            composite=0.5, cue_count=10, flagged=flagged, readability=None, signals=None
        ),
        source=source,
    )


def test_empty_store_totals_zero_and_does_not_raise(store):
    assert store.count_results(view="flagged") == 0
    assert store.count_results(view="all") == 0
    assert store.count_results(view="all", source="existing_audit") == 0


def test_total_counts_every_matching_row_not_a_page(store):
    for i in range(250):
        _add(store, i, flagged=True)
    # The page is capped; the total is not. That difference is the whole bug.
    page = store.list_results(view="flagged", limit=100, offset=0)
    assert len(page) == 100
    assert store.count_results(view="flagged") == 250


def test_total_respects_the_view_filter(store):
    for i in range(10):
        _add(store, i, flagged=True)
    for i in range(100, 105):
        _add(store, i, flagged=False)
    assert store.count_results(view="flagged") == 10
    assert store.count_results(view="all") == 15


def test_total_respects_the_source_filter(store):
    for i in range(6):
        _add(store, i, flagged=True, source="existing_audit")
    for i in range(50, 53):
        _add(store, i, flagged=True, source="subgen")
    assert store.count_results(view="flagged", source="existing_audit") == 6
    assert store.count_results(view="flagged", source="subgen") == 3
    assert store.count_results(view="flagged") == 9


def test_paging_with_the_total_covers_every_row_once(store):
    for i in range(230):
        _add(store, i, flagged=True)
    total = store.count_results(view="flagged")
    seen: list[str] = []
    for off in range(0, total, 50):
        seen.extend(r["canonical_path"] for r in store.list_results(view="flagged", limit=50, offset=off))
    assert len(seen) == 230
    assert len(set(seen)) == 230, "no row duplicated or skipped while paging"
