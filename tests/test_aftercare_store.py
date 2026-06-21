"""#156 aftercare store + migration."""

from __future__ import annotations


def _migrated_db(tmp_path):
    from subarr.migrate import run_migrations

    db = tmp_path / "a.db"
    run_migrations(db)
    return db


def test_migration_creates_aftercare_table(tmp_path):
    import sqlite3

    db = _migrated_db(tmp_path)
    conn = sqlite3.connect(str(db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(aftercare_results)")}
    assert {
        "id",
        "canonical_path",
        "completed_at",
        "composite",
        "cue_count",
        "flagged",
        "readability_json",
        "signals_json",
        "source",
        "preview",
        "reviewed_at",
        "created_at",
    } <= cols


def _store(tmp_path):
    from subarr.aftercare_store import AfterCareStore

    return AfterCareStore(_migrated_db(tmp_path))


def _ev(flagged, composite=40.0):
    from subarr.aftercare import AftercareEvaluation

    return AftercareEvaluation(
        composite=composite,
        cue_count=10,
        flagged=flagged,
        readability={"issues": []},
        signals={"repeated_line_ratio": 0.0, "canned_phrase_hits": 0},
    )


def test_record_and_pending_count(tmp_path):
    s = _store(tmp_path)
    s.record(canonical_path="TV/A/e1.mkv", completed_at=1.0, evaluation=_ev(True), source="subgenscan")
    s.record(
        canonical_path="TV/A/e2.mkv",
        completed_at=1.0,
        evaluation=_ev(False, composite=95.0),
        source="subgenscan",
    )
    assert s.pending_count() == 1  # only the flagged one


def test_list_results_views_and_latest_per_path(tmp_path):
    s = _store(tmp_path)
    # same path twice — a requeue: first flagged, then clean & newer
    s.record(canonical_path="TV/A/e1.mkv", completed_at=1.0, evaluation=_ev(True), source="subgenscan")
    s.record(
        canonical_path="TV/A/e1.mkv", completed_at=2.0, evaluation=_ev(False, composite=95.0), source="manual"
    )
    flagged = s.list_results(view="flagged", limit=50, offset=0)
    assert flagged == []  # latest is clean -> not pending
    all_rows = s.list_results(view="all", limit=50, offset=0)
    assert len(all_rows) == 1  # latest-per-path
    assert all_rows[0]["composite"] == 95.0


def test_mark_reviewed(tmp_path):
    s = _store(tmp_path)
    s.record(canonical_path="TV/A/e1.mkv", completed_at=1.0, evaluation=_ev(True), source="subgenscan")
    row = s.list_results(view="flagged", limit=50, offset=0)[0]
    assert s.mark_reviewed(row["id"]) is True
    assert s.pending_count() == 0
    assert s.mark_reviewed(999999) is False


def test_preview_round_trips(tmp_path):
    # #216: the sanitized cue snippet persists and comes back on read
    s = _store(tmp_path)
    s.record(
        canonical_path="TV/A/e1.en.srt",
        completed_at=1.0,
        evaluation=_ev(True),
        source="existing_audit",
        preview="Downloaded from spam.com",
    )
    row = s.list_results(view="all", limit=50, offset=0)[0]
    assert row["preview"] == "Downloaded from spam.com"


def test_preview_defaults_null_for_generated_rows(tmp_path):
    s = _store(tmp_path)
    s.record(canonical_path="TV/A/e1.mkv", completed_at=1.0, evaluation=_ev(False), source="subgenscan")
    assert s.list_results(view="all", limit=50, offset=0)[0]["preview"] is None


def test_list_results_source_filter(tmp_path):
    # #216: the review page filters audited external subs from watcher rows
    s = _store(tmp_path)
    s.record(
        canonical_path="TV/A/ext.en.srt", completed_at=1.0, evaluation=_ev(True), source="existing_audit"
    )
    s.record(canonical_path="TV/A/gen.mkv", completed_at=1.0, evaluation=_ev(True), source="subgenscan")
    audited = s.list_results(view="all", limit=50, offset=0, source="existing_audit")
    assert [r["canonical_path"] for r in audited] == ["TV/A/ext.en.srt"]
    everything = s.list_results(view="all", limit=50, offset=0)
    assert len(everything) == 2


def test_mark_all_reviewed_clears_every_pending(tmp_path):
    # #313 bulk-ack: one action clears a large first-run backlog.
    s = _store(tmp_path)
    s.record(canonical_path="TV/A/e1.mkv", completed_at=1.0, evaluation=_ev(True), source="subgenscan")
    s.record(canonical_path="TV/A/e2.mkv", completed_at=1.0, evaluation=_ev(True), source="subgenscan")
    s.record(canonical_path="TV/B/e1.mkv", completed_at=1.0, evaluation=_ev(True), source="existing_audit")
    assert s.mark_all_reviewed() == 3
    assert s.mark_all_reviewed() == 0  # idempotent — nothing left pending


def test_mark_all_reviewed_honours_source_filter(tmp_path):
    s = _store(tmp_path)
    s.record(canonical_path="TV/A/e1.mkv", completed_at=1.0, evaluation=_ev(True), source="subgenscan")
    s.record(canonical_path="TV/B/e1.mkv", completed_at=1.0, evaluation=_ev(True), source="existing_audit")
    assert s.mark_all_reviewed(source="existing_audit") == 1  # only the audited one
    assert s.mark_all_reviewed() == 1  # the subgenscan one still pending
