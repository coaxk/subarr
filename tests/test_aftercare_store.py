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


# ── server-side search + pagination regression coverage ────────────────────


def _seed(s, *paths, source="subgenscan", preview=None, flagged=True):
    for i, p in enumerate(paths):
        s.record(
            canonical_path=p,
            completed_at=1.0 + i,
            evaluation=_ev(flagged),
            source=source,
            preview=preview,
        )


def test_search_matches_and_misses(tmp_path):
    s = _store(tmp_path)
    _seed(s, "TV/A/e1.mkv", "TV/B/f1.mkv")
    assert s.count_results(view="all", search="e1") == 1
    assert [r["canonical_path"] for r in s.list_results(view="all", limit=50, offset=0, search="e1")] == [
        "TV/A/e1.mkv"
    ]
    assert s.count_results(view="all", search="nomatch-xyz") == 0
    assert s.list_results(view="all", limit=50, offset=0, search="nomatch-xyz") == []
    # no search -> every latest-per-path row
    assert s.count_results(view="all") == 2
    assert len(s.list_results(view="all", limit=50, offset=0)) == 2


def test_search_case_insensitive(tmp_path):
    s = _store(tmp_path)
    s.record(
        canonical_path="TV/A/e1.mkv",
        completed_at=1.0,
        evaluation=_ev(True),
        source="subgenscan",
        preview="CLEAN sub",
    )
    s.record(
        canonical_path="TV/A/E2.mkv",
        completed_at=2.0,
        evaluation=_ev(True),
        source="SUBGENSCAN",
        preview="Downloaded from SPAM.com",
    )
    # case-insensitive over canonical_path
    assert s.count_results(view="all", search="tv/a") == 2
    assert s.count_results(view="all", search="TV/A") == 2
    # case-insensitive over source (E2's source is uppercase SUBGENSCAN)
    assert s.count_results(view="all", search="subgenscan") == 2
    assert s.count_results(view="all", search="SUBGENSCAN") == 2
    # case-insensitive over preview (only E2's preview contains "spam")
    assert s.count_results(view="all", search="spam") == 1
    assert s.count_results(view="all", search="SPAM") == 1


def test_count_and_page_agree(tmp_path):
    s = _store(tmp_path)
    for i in range(5):
        s.record(
            canonical_path=f"TV/A/e{i}.mkv", completed_at=1.0 + i, evaluation=_ev(True), source="subgenscan"
        )
    # count is the full matching total even when the page is smaller
    assert s.count_results(view="flagged") == 5
    assert len(s.list_results(view="flagged", limit=2, offset=0)) == 2
    # search narrows count AND page together
    assert s.count_results(view="flagged", search="e3") == 1
    page = s.list_results(view="flagged", limit=10, offset=0, search="e3")
    assert len(page) == 1
    assert page[0]["canonical_path"] == "TV/A/e3.mkv"


def test_search_offset_limit_behavior(tmp_path):
    s = _store(tmp_path)
    for i in range(10):
        s.record(
            canonical_path=f"TV/A/e{i}.mkv", completed_at=1.0 + i, evaluation=_ev(True), source="subgenscan"
        )
    # paginate the full matching set (ORDER BY completed_at DESC -> e9..e0)
    assert len(s.list_results(view="flagged", limit=3, offset=0)) == 3
    assert len(s.list_results(view="flagged", limit=3, offset=6)) == 3  # e3,e2,e1
    assert len(s.list_results(view="flagged", limit=3, offset=8)) == 2  # e1,e0 (page tail)
    assert s.list_results(view="flagged", limit=3, offset=20) == []
    # search + offset slices the matching set ("e" matches all 10)
    assert s.count_results(view="flagged", search="e") == 10
    assert len(s.list_results(view="flagged", limit=3, offset=2, search="e")) == 3


def test_search_with_source_and_view(tmp_path):
    s = _store(tmp_path)
    _seed(s, "TV/A/a1.mkv", "TV/A/a2.mkv", "TV/B/b1.mkv", source="existing_audit")
    s.record(
        canonical_path="TV/B/b1.mkv",
        completed_at=1.0,
        evaluation=_ev(False, composite=95.0),
        source="existing_audit",
    )
    # view=all + source + search: all existing_audit rows match the path prefix
    assert s.count_results(view="all", source="existing_audit", search="TV/") == 3
    assert len(s.list_results(view="all", limit=50, offset=0, source="existing_audit", search="TV/")) == 3
    # view=flagged excludes the clean row
    assert s.count_results(view="flagged", source="existing_audit", search="TV/") == 2
    # the clean row is in view=all but not in view=flagged
    assert s.count_results(view="all", source="existing_audit", search="b1") == 1
    assert s.count_results(view="flagged", source="existing_audit", search="b1") == 0


def test_search_source_and_preview_respect_view_and_latest_predicates(tmp_path):
    s = _store(tmp_path)
    s.record(
        canonical_path="TV/A/e1.mkv",
        completed_at=1.0,
        evaluation=_ev(True),
        source="old_source",
        preview="old_preview",
    )
    s.record(
        canonical_path="TV/A/e1.mkv",
        completed_at=2.0,
        evaluation=_ev(True),
        source="new_source",
        preview="new_preview",
    )
    s.record(
        canonical_path="TV/A/clean.mkv",
        completed_at=1.0,
        evaluation=_ev(False),
        source="new_source",
        preview="new_preview",
    )

    assert s.count_results(view="flagged", search="new_source") == 1
    assert len(s.list_results(view="flagged", limit=50, offset=0, search="new_source")) == 1
    assert s.count_results(view="all", search="old_source") == 0
    assert s.count_results(view="all", search="new_preview") == 2


def test_latest_per_path_holds_under_search(tmp_path):
    s = _store(tmp_path)
    # same path twice — requeued: first flagged, then clean & newer
    s.record(canonical_path="TV/A/e1.mkv", completed_at=1.0, evaluation=_ev(True), source="subgenscan")
    s.record(
        canonical_path="TV/A/e1.mkv", completed_at=2.0, evaluation=_ev(False, composite=95.0), source="manual"
    )
    s.record(canonical_path="TV/A/e2.mkv", completed_at=1.0, evaluation=_ev(True), source="subgenscan")
    # search "e1" returns ONLY the latest row for that path
    rows = s.list_results(view="all", limit=50, offset=0, search="e1")
    assert len(rows) == 1
    assert rows[0]["composite"] == 95.0  # the newer clean row, not the flagged one
    assert s.count_results(view="all", search="e1") == 1
    # flagged view: latest is clean -> e1 not pending
    assert s.count_results(view="flagged", search="e1") == 0


def test_search_wildcards_and_quotes_match_literally(tmp_path):
    s = _store(tmp_path)
    _seed(s, "TV/A/100%.mkv", "TV/A/plain.mkv", "TV/A/other.mkv", "TV/A/bs.mkv")
    s.record(
        canonical_path="TV/A/100%.mkv",
        completed_at=1.0,
        evaluation=_ev(True),
        source="subgenscan",
        preview="50% off",
    )
    s.record(
        canonical_path="TV/A/plain.mkv",
        completed_at=1.0,
        evaluation=_ev(True),
        source="subgenscan",
        preview="a_b",
    )
    s.record(
        canonical_path="TV/A/other.mkv",
        completed_at=1.0,
        evaluation=_ev(True),
        source="subgenscan",
        preview="it's fine",
    )
    s.record(
        canonical_path="TV/A/bs.mkv",
        completed_at=1.0,
        evaluation=_ev(True),
        source="subgenscan",
        preview="C:\\path\\to",
    )
    # literal % matches only the row with a literal % (not every row -> proves escaping)
    assert s.count_results(view="all", search="%") == 1
    assert s.list_results(view="all", limit=50, offset=0, search="%")[0]["canonical_path"] == "TV/A/100%.mkv"
    # literal underscore must NOT act as a single-char wildcard
    assert s.count_results(view="all", search="a_b") == 1
    assert s.count_results(view="all", search="_") == 1
    # quote chars are just bound text — no injection/escaping breakage
    assert s.count_results(view="all", search="it's") == 1
    assert (
        s.list_results(view="all", limit=50, offset=0, search="it's")[0]["canonical_path"] == "TV/A/other.mkv"
    )
    assert s.count_results(view="all", search="'") == 1
    # literal backslash
    assert s.count_results(view="all", search="\\") == 1
    assert s.list_results(view="all", limit=50, offset=0, search="\\")[0]["canonical_path"] == "TV/A/bs.mkv"


def test_mark_all_reviewed_honours_source_filter(tmp_path):
    s = _store(tmp_path)
    s.record(canonical_path="TV/A/e1.mkv", completed_at=1.0, evaluation=_ev(True), source="subgenscan")
    s.record(canonical_path="TV/B/e1.mkv", completed_at=1.0, evaluation=_ev(True), source="existing_audit")
    assert s.mark_all_reviewed(source="existing_audit") == 1  # only the audited one
    assert s.mark_all_reviewed() == 1  # the subgenscan one still pending
