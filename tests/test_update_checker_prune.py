"""Orphan-prune: update_checks rows for products no longer tracked must be
dropped, else states() renders a ghost on the Updates page — e.g. the vanilla
'subgen' row lingering after the box switched to the subarr-subgen fork.
"""

from __future__ import annotations

import sqlite3


def _seed(db, product):
    conn = sqlite3.connect(str(db), isolation_level=None)
    conn.execute(
        "INSERT INTO update_checks (product, repo, current_version, latest_version, "
        "latest_released_at, release_notes_url, is_breaking, checked_at, last_error, missed_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (product, f"x/{product}", "1", "1", None, None, 0, 0.0, None, None),
    )
    conn.close()


def test_prune_untracked_drops_orphan_products(tmp_path):
    from subarr.migrate import run_migrations
    from subarr.update_checker import UpdateChecker

    db = tmp_path / "u.db"
    run_migrations(db)
    _seed(db, "subarr-subgen")
    _seed(db, "subgen")  # orphan: not tracked when running the fork

    uc = UpdateChecker(
        db_path=db,
        products={"subarr": "coaxk/subarr", "subarr-subgen": "coaxk/subarr-subgen"},
    )
    removed = uc._prune_untracked()

    assert removed == 1
    names = {s.product for s in uc.states()}
    assert names == {"subarr-subgen"}  # orphan 'subgen' gone, tracked row kept


def test_prune_untracked_noop_when_all_tracked(tmp_path):
    from subarr.migrate import run_migrations
    from subarr.update_checker import UpdateChecker

    db = tmp_path / "u.db"
    run_migrations(db)
    _seed(db, "subarr-subgen")

    uc = UpdateChecker(
        db_path=db,
        products={"subarr": "coaxk/subarr", "subarr-subgen": "coaxk/subarr-subgen"},
    )
    assert uc._prune_untracked() == 0
    assert {s.product for s in uc.states()} == {"subarr-subgen"}
