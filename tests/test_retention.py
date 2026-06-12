"""#197 — every table has an explicit growth answer; these pin the prunes.

Unbounded-growth tables get boot-time pruning. The protective rules matter
more than the cutoffs: PENDING manual-confirm walks are never pruned (the
user's open queue), and flagged-but-unreviewed aftercare rows are never
pruned (the user's review backlog).
"""

from __future__ import annotations

import time


def _db(tmp_path):
    from subarr.migrate import run_migrations

    db = tmp_path / "t.db"
    run_migrations(db)
    return db


OLD = time.time() - 400 * 86400
FRESH = time.time() - 3600


def test_error_events_prune(subarr_env, tmp_path):
    from subarr.error_store import ErrorStore

    s = ErrorStore(_db(tmp_path))
    s.record("OldError", when=OLD)
    s.record("FreshError", when=FRESH)
    s.prune(days=60)
    counts = s.counts_since(0)
    assert "OldError" not in counts and "FreshError" in counts


def test_scans_prune_keeps_recent(subarr_env, tmp_path):
    import sqlite3

    from subarr.scan_store import ScanStore

    db = _db(tmp_path)
    s = ScanStore(db)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO scans (id, created_at, status, paths_json, results_json) VALUES (?,?,?,?,?)",
        ("old1", OLD, "completed", "[]", "[]"),
    )
    conn.execute(
        "INSERT INTO scans (id, created_at, status, paths_json, results_json) VALUES (?,?,?,?,?)",
        ("new1", FRESH, "completed", "[]", "[]"),
    )
    conn.commit()
    conn.close()
    s.prune(days=180)
    assert s.get("old1") is None
    assert s.get("new1") is not None


def test_aftercare_prune_never_touches_unreviewed_flags(subarr_env, tmp_path):
    import sqlite3

    db = _db(tmp_path)
    from subarr.aftercare_store import AfterCareStore

    s = AfterCareStore(db)
    conn = sqlite3.connect(str(db))

    def ins(cid, completed, flagged, reviewed):
        conn.execute(
            "INSERT INTO aftercare_results (canonical_path, completed_at, composite, cue_count, "
            "flagged, reviewed_at, source, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (cid, completed, 0.5, 100, flagged, reviewed, "subgenscan", completed),
        )

    ins("old-clean.mkv", OLD, 0, None)  # old, unflagged → prune
    ins("old-flag-unreviewed.mkv", OLD, 1, None)  # old, flagged, NOT reviewed → KEEP
    ins("old-flag-reviewed.mkv", OLD, 1, OLD + 60)  # old, flagged, reviewed → prune
    ins("fresh.mkv", FRESH, 0, None)  # fresh → keep
    conn.commit()
    conn.close()

    s.prune(days=365)

    conn = sqlite3.connect(str(db))
    left = {r[0] for r in conn.execute("SELECT canonical_path FROM aftercare_results").fetchall()}
    conn.close()
    assert left == {"old-flag-unreviewed.mkv", "fresh.mkv"}


def test_pending_walks_prune_never_touches_pending(subarr_env, tmp_path):
    import sqlite3

    db = _db(tmp_path)
    from subarr.pending_store import PendingStore

    s = PendingStore(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=ON")

    def walk(wid, created, status):
        conn.execute(
            "INSERT INTO pending_walks (id, created_at, status) VALUES (?,?,?)",
            (wid, created, status),
        )
        conn.execute(
            "INSERT INTO pending_decisions (walk_id, item_json) VALUES (?,?)",
            (wid, "{}"),
        )

    walk("old-resolved", OLD, "approved_all")  # prune (+ decisions via CASCADE)
    walk("old-pending", OLD, "pending")  # KEEP — user's open queue
    walk("fresh-resolved", FRESH, "rejected")  # keep (fresh)
    conn.commit()
    conn.close()

    s.prune(days=30)

    conn = sqlite3.connect(str(db))
    walks = {r[0] for r in conn.execute("SELECT id FROM pending_walks").fetchall()}
    decisions = conn.execute("SELECT COUNT(*) FROM pending_decisions").fetchone()[0]
    conn.close()
    assert walks == {"old-pending", "fresh-resolved"}
    assert decisions == 2  # the old-resolved walk's decision went with it
