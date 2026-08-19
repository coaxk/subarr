"""#451 phase-1 store/migration tests.

Covers migration 030_pr451_subtitle_tuning (provenance + advisory
text-language-check, consolidated pre-merge from the two-file 030/031 form)
(fresh + upgraded DBs, nullable legacy rows, unique open-row backstop) and the
provenance store extensions:
LedgerEntry field round trips/order, provenance_conflict tri-state/stickiness,
record_webhook_and_complete (evidence writes, normalized comparison, exactly-once
completion, concurrent delivery, idempotent zero-row behavior).
"""

from __future__ import annotations

import shutil
import sqlite3
import threading
from pathlib import Path

import pytest

from subarr.migrate import run_migrations
from subarr.pending_queue import PendingQueueStore
from subarr.provenance import ProvenanceStore

MIGRATIONS_DIR = Path(__file__).parent.parent / "src" / "subarr" / "migrations"

# Documented field order for the ledger serialization (P1-S3 contract).
_PROVENANCE_KEYS = [
    "task",
    "source_language",
    "target_language",
    "submission_origin",
    "webhook_event",
    "webhook_language",
    "webhook_subtitle",
    "provenance_conflict",
]

_LEDGER_COLS = set(_PROVENANCE_KEYS + ["bazarr_scan_triggered_at"])


def _fresh_store(tmp_path: Path) -> ProvenanceStore:
    db = tmp_path / "p.db"
    run_migrations(db)
    return ProvenanceStore(db)


# ─── migration: fresh DB ────────────────────────────────────────────────


def _cols(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {r[1]: r[2] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_migration_030_fresh_db_columns(tmp_path):
    db = tmp_path / "m.db"
    run_migrations(db)
    conn = sqlite3.connect(str(db))
    try:
        ledger = _cols(conn, "subs_generated")
        for col in [
            "task",
            "source_language",
            "target_language",
            "submission_origin",
            "webhook_event",
            "webhook_language",
            "webhook_subtitle",
        ]:
            assert ledger.get(col) == "TEXT", f"subs_generated.{col} should be TEXT"
        assert ledger.get("provenance_conflict") == "INTEGER"

        pq = _cols(conn, "pending_queue")
        for col in ["source_language", "target_language", "submission_origin"]:
            assert pq.get(col) == "TEXT", f"pending_queue.{col} should be TEXT"
        # task predates 030 (014); it must remain present + nullable.
        assert pq.get("task") == "TEXT"

        # Consolidated 030 also adds the advisory text-language-check column.
        ar = _cols(conn, "aftercare_results")
        assert ar.get("text_lang_check_json") == "TEXT"

        # All new columns are nullable with no default -> legacy rows stay NULL.
        for col in _LEDGER_COLS:
            dflt = conn.execute(
                f"SELECT dflt_value FROM pragma_table_info('subs_generated') WHERE name='{col}'"
            ).fetchone()
            assert dflt[0] is None, f"{col} must have no default"

        # Unique open-row backstop partial index.
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='uq_subs_generated_open'"
        ).fetchone()
        assert idx is not None, "uq_subs_generated_open index missing"
    finally:
        conn.close()


def test_migration_030_upgrade_legacy_rows_stay_null(tmp_path):
    """A 029-era DB upgraded to HEAD keeps its rows with NULL provenance, and
    picks up the new columns + backstop index."""
    era = tmp_path / "migs"
    era.mkdir()
    for f in sorted(MIGRATIONS_DIR.glob("*.sql"))[:29]:
        shutil.copy(f, era / f.name)
    db = tmp_path / "up.db"
    applied = run_migrations(db, migrations_dir=era)
    assert applied and max(m.version for m in applied) == 29

    # Plant legacy rows on the 029-era schema (no provenance columns yet).
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO pending_queue (id, canonical_path, position, priority, status, source, created_at) "
            "VALUES ('legacy1', 'TV/A/s01e01.mkv', 0, 1, 'pending', 'gaps', 1000.0)"
        )
        conn.execute(
            "INSERT INTO subs_generated (canonical_path, source, queued_at) "
            "VALUES ('TV/A/s01e01.mkv', 'subgenscan', 1000.0)"
        )
        conn.commit()
    finally:
        conn.close()

    # Upgrade with the FULL current set (includes the consolidated 030: aftercare
    # text-LID result column).
    upgraded = run_migrations(db)
    assert max(m.version for m in upgraded) == 30

    conn = sqlite3.connect(str(db))
    try:
        # Legacy rows keep NULL in every new column.
        pq = conn.execute(
            "SELECT source_language, target_language, submission_origin FROM pending_queue WHERE id='legacy1'"
        ).fetchone()
        assert pq == (None, None, None)
        lg = conn.execute(
            "SELECT task, source_language, target_language, submission_origin, "
            "webhook_event, webhook_language, webhook_subtitle, provenance_conflict "
            "FROM subs_generated WHERE canonical_path='TV/A/s01e01.mkv'"
        ).fetchone()
        assert lg == (None, None, None, None, None, None, None, None)
        # Existing data + the backstop index survived.
        assert conn.execute(
            "SELECT source FROM subs_generated WHERE canonical_path='TV/A/s01e01.mkv'"
        ).fetchone() == ("subgenscan",)
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='uq_subs_generated_open'"
        ).fetchone()
        assert idx is not None
        # The consolidated 030 freshly adds the advisory text-LID result column.
        ar = _cols(conn, "aftercare_results")
        assert ar.get("text_lang_check_json") == "TEXT"
    finally:
        conn.close()


def test_unique_open_row_backstop_blocks_second(tmp_path):
    db = tmp_path / "u.db"
    run_migrations(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO subs_generated (canonical_path, source, queued_at) "
            "VALUES ('/m/A.mkv', 'subgenscan', 1000.0)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO subs_generated (canonical_path, source, queued_at) "
                "VALUES ('/m/A.mkv', 'subgenscan', 2000.0)"
            )
        # A COMPLETED row no longer matches the partial index -> new row allowed.
        conn.execute("UPDATE subs_generated SET completed_at=3000.0 WHERE canonical_path='/m/A.mkv'")
        conn.execute(
            "INSERT INTO subs_generated (canonical_path, source, queued_at) "
            "VALUES ('/m/A.mkv', 'subgenscan', 4000.0)"
        )
        conn.commit()
        assert (
            conn.execute("SELECT COUNT(*) FROM subs_generated WHERE canonical_path='/m/A.mkv'").fetchone()[0]
            == 2
        )
    finally:
        conn.close()


# ─── LedgerEntry round trips / serialization ORDER ──────────────────────


def test_record_round_trips_submission_claims(tmp_path):
    store = _fresh_store(tmp_path)
    lid = store.record(
        canonical_path="/m/A.mkv",
        scan_id="s1",
        task="translate",
        source_language="en",
        target_language="es",
        submission_origin="manual",
    )
    for getter in (store.query_by_path("/m/A.mkv"), store.pending(), store.recent()):
        e = next(x for x in getter if x.id == lid)
        assert e.task == "translate"
        assert e.source_language == "en"
        assert e.target_language == "es"
        assert e.submission_origin == "manual"


def test_ledger_to_dict_key_order_and_conflict_exposure(tmp_path):
    store = _fresh_store(tmp_path)
    # conflict tri-state: unset -> None (raw NULL)
    store.record(canonical_path="/m/A.mkv", scan_id="s1", task="translate", target_language="es")
    d = store.query_by_path("/m/A.mkv")[0].to_dict()
    keys = list(d.keys())
    assert keys[-8:] == _PROVENANCE_KEYS, f"provenance keys must be in documented order: {keys}"
    assert d["provenance_conflict"] is None
    assert d["task"] == "translate" and d["target_language"] == "es"

    # Force a disagreement via webhook, then re-check to_dict bool mapping.
    store.record_webhook_and_complete(
        canonical_path="/m/A.mkv", event="translated", language="fr", received_at=100.0
    )
    e = store.query_by_path("/m/A.mkv")[0]
    assert e.provenance_conflict == 1  # raw INTEGER in the dataclass field
    assert e.to_dict()["provenance_conflict"] is True


# ─── record_webhook_and_complete ────────────────────────────────────────


def test_no_open_row_returns_zero(tmp_path):
    store = _fresh_store(tmp_path)
    assert store.record_webhook_and_complete(canonical_path="/m/never.mkv", event="transcribed") == 0
    assert store.pending() == []
    assert store.query_by_path("/m/never.mkv") == []


def test_completes_and_writes_evidence_using_received_at(tmp_path):
    store = _fresh_store(tmp_path)
    lid = store.record(canonical_path="/m/A.mkv", scan_id="s1", task="translate", target_language="es")
    ret = store.record_webhook_and_complete(
        canonical_path="/m/A.mkv",
        event="translated",
        language="es",
        subtitle="/media/out/A.en.srt",
        received_at=1234.5,
    )
    assert ret == lid
    e = store.query_by_path("/m/A.mkv")[0]
    assert e.completed_at == 1234.5
    assert e.webhook_event == "translated"
    assert e.webhook_language == "es"
    assert e.webhook_subtitle == "/media/out/A.en.srt"
    assert e.provenance_conflict == 0  # task + target agree
    # Completed row leaves the pending set.
    assert store.pending() == []
    assert store.completed_without_bazarr() == []


def test_repeated_identical_delivery_is_idempotent(tmp_path):
    store = _fresh_store(tmp_path)
    lid = store.record(canonical_path="/m/A.mkv", scan_id="s1", task="transcribe")
    first = store.record_webhook_and_complete(
        canonical_path="/m/A.mkv", event="transcribed", received_at=100.0
    )
    assert first == lid
    # A second/polled delivery: row already completed -> no open row -> 0.
    assert (
        store.record_webhook_and_complete(canonical_path="/m/A.mkv", event="transcribed", received_at=200.0)
        == 0
    )
    e = store.query_by_path("/m/A.mkv")[0]
    assert e.completed_at == 100.0  # exactly-once: first stamp kept


def test_null_only_evidence_writes_preserve_first_writer(tmp_path):
    """Seed webhook_event (without completing), then deliver a full call: the
    pre-existing event is preserved (NULL-only) and only the NULL columns fill."""
    store = _fresh_store(tmp_path)
    lid = store.record(canonical_path="/m/A.mkv", scan_id="s1", task="translate", target_language="es")
    # Simulate a first delivery that only carried event evidence.
    store._conn.execute("UPDATE subs_generated SET webhook_event = 'translated' WHERE id = ?", (lid,))
    store.record_webhook_and_complete(
        canonical_path="/m/A.mkv", event="transcribed", language="fr", subtitle="/x.srt", received_at=50.0
    )
    e = store.query_by_path("/m/A.mkv")[0]
    # First writer's event wins (identical? no — first writer wins, period).
    assert e.webhook_event == "translated"
    # Language/subtitle were NULL, so they fill from THIS delivery.
    assert e.webhook_language == "fr"
    assert e.webhook_subtitle == "/x.srt"
    # Comparison uses the stored event (translated == translate) and the new
    # language (es vs fr) -> conflict.
    assert e.provenance_conflict == 1


def test_conflict_tri_state_no_comparison_when_claims_absent(tmp_path):
    store = _fresh_store(tmp_path)
    # No submission claims at all -> nothing comparable -> conflict NULL.
    store.record(canonical_path="/m/A.mkv", scan_id="s1")
    store.record_webhook_and_complete(canonical_path="/m/A.mkv", event="transcribed", language="es")
    assert store.query_by_path("/m/A.mkv")[0].provenance_conflict is None

    # A submission TASK but NO webhook event, and a webhook LANGUAGE but NO
    # submission target -> neither pair is complete -> nothing comparable -> NULL.
    store.record(canonical_path="/m/B.mkv", scan_id="s2", task="translate")  # no target claim
    store.record_webhook_and_complete(canonical_path="/m/B.mkv", language="es")  # no event claim
    assert store.query_by_path("/m/B.mkv")[0].provenance_conflict is None


def test_conflict_zero_on_agreement(tmp_path):
    store = _fresh_store(tmp_path)
    store.record(canonical_path="/m/A.mkv", scan_id="s1", task="translate", target_language="es")
    store.record_webhook_and_complete(canonical_path="/m/A.mkv", event="translated", language="es")
    assert store.query_by_path("/m/A.mkv")[0].provenance_conflict == 0


def test_normalized_comparison_casefold_and_vocabulary(tmp_path):
    store = _fresh_store(tmp_path)
    # 'Transcribe' (casefold) and submission 'transcribe' agree; target 'EN'
    # vs webhook 'en' agree (strip + casefold).
    store.record(canonical_path="/m/A.mkv", scan_id="s1", task="transcribe", target_language="EN")
    store.record_webhook_and_complete(canonical_path="/m/A.mkv", event="Transcribed", language="en")
    assert store.query_by_path("/m/A.mkv")[0].provenance_conflict == 0


def test_conflict_one_on_language_disagreement(tmp_path):
    store = _fresh_store(tmp_path)
    store.record(canonical_path="/m/A.mkv", scan_id="s1", task="translate", target_language="es")
    store.record_webhook_and_complete(canonical_path="/m/A.mkv", event="translated", language="fr")
    assert store.query_by_path("/m/A.mkv")[0].provenance_conflict == 1


def test_conflict_one_on_task_disagreement(tmp_path):
    store = _fresh_store(tmp_path)
    store.record(canonical_path="/m/A.mkv", scan_id="s1", task="translate", target_language="es")
    store.record_webhook_and_complete(canonical_path="/m/A.mkv", event="transcribed", language="es")
    assert store.query_by_path("/m/A.mkv")[0].provenance_conflict == 1


def test_conflict_sticky_never_cleared(tmp_path):
    """Once provenance_conflict=1 is present on an open row, a later agreeing
    call must NOT downgrade it (the UPDATE is guarded provenance_conflict != 1)."""
    store = _fresh_store(tmp_path)
    lid = store.record(canonical_path="/m/A.mkv", scan_id="s1", task="translate", target_language="es")
    # Simulate a state where the comparison already flagged conflict=1 but the
    # row is still OPEN (would not normally be produced by this API, but the
    # guard must hold regardless of how 1 got there).
    store._conn.execute("UPDATE subs_generated SET provenance_conflict = 1 WHERE id = ?", (lid,))
    # An agreeing delivery must NOT clear the flag.
    store.record_webhook_and_complete(
        canonical_path="/m/A.mkv", event="translated", language="es", received_at=77.0
    )
    e = store.query_by_path("/m/A.mkv")[0]
    assert e.provenance_conflict == 1
    assert e.to_dict()["provenance_conflict"] is True
    assert e.completed_at == 77.0  # completion still proceeds


def test_concurrent_completion_exactly_once(tmp_path):
    """Two stores on one DB race to complete the same open row: exactly one
    wins, completed_at is set once, and no duplicate/completion corruption."""
    db = tmp_path / "cc.db"
    run_migrations(db)
    seed = ProvenanceStore(db)
    lid = seed.record(canonical_path="/m/A.mkv", scan_id="s1", task="translate", target_language="es")
    seed.close()

    store_a = ProvenanceStore(db)
    store_b = ProvenanceStore(db)
    barrier = threading.Barrier(2)
    results: list[int] = []

    def deliver(st: ProvenanceStore, ts: float) -> None:
        barrier.wait()
        r = st.record_webhook_and_complete(
            canonical_path="/m/A.mkv", event="translated", language="es", received_at=ts
        )
        results.append(r)

    ta = threading.Thread(target=deliver, args=(store_a, 1000.0))
    tb = threading.Thread(target=deliver, args=(store_b, 2000.0))
    ta.start()
    tb.start()
    ta.join()
    tb.join()

    store_a.close()
    store_b.close()

    # Exactly one delivery completed; the other observed the row as closed -> 0.
    assert sorted(results) == [0, lid]
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT completed_at FROM subs_generated WHERE canonical_path='/m/A.mkv'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] in (1000.0, 2000.0)
    finally:
        conn.close()


# ─── PendingJob provenance field round trips (P1-S2) ────────────────────


def _pending_store(tmp_path: Path) -> PendingQueueStore:
    db = tmp_path / "q.db"
    run_migrations(db)
    return PendingQueueStore(db)


def test_enqueue_persists_provenance_claims(tmp_path):
    store = _pending_store(tmp_path)
    j = store.enqueue(
        "TV/A/s01e01.mkv",
        source="manual",
        task="translate",
        source_language="en",
        target_language="es",
        submission_origin="manual",
    )
    fetched = store.get(j.id)
    assert (fetched.task, fetched.source_language, fetched.target_language, fetched.submission_origin) == (
        "translate",
        "en",
        "es",
        "manual",
    )
    d = fetched.to_dict()
    assert d["source_language"] == "en"
    assert d["target_language"] == "es"
    assert d["submission_origin"] == "manual"
    # New fields sit after radarr_movie_id in the serialized order.
    assert list(d.keys())[-3:] == ["source_language", "target_language", "submission_origin"]


def test_enqueue_provenance_defaults_none(tmp_path):
    store = _pending_store(tmp_path)
    j = store.enqueue("TV/A/s01e01.mkv", source="manual")
    f = store.get(j.id)
    assert (f.task, f.source_language, f.target_language, f.submission_origin) == (
        None,
        None,
        None,
        None,
    )
