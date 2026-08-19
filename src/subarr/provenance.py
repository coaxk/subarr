"""Provenance ledger.

Records every transcribe job subarr submits to subgen. Two distinct
purposes:

1. **Source-of-truth for "who wrote this sub?"** — the v1.1
   `GET /api/provenance/<path>` endpoint joins this ledger with Bazarr's
   history endpoint + the filename suffix to answer that question
   end-to-end. srt-cleaner integration (later) reads this ledger to
   know which subs to evaluate.

2. **Completion tracking** — rows are written at submit time with
   completed_at=NULL. The completion-watcher background task polls
   subgen's /queue, marks completed_at when the path leaves both
   queued+processing, and (if a series_id is known) triggers Bazarr's
   scan-disk so Bazarr picks up the new sub without waiting for its
   periodic scan.

Same SQLite file as the scan store. WAL mode + a single Lock around
the connection — no per-thread state.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .data_persistence import apply_journal_mode

log = logging.getLogger(__name__)


SOURCE_SUBGENSCAN = "subgenscan"
SOURCE_BAZARR_VIA_SUBGEN = "bazarr-via-subgen"
SOURCE_BAZARR_EXTERNAL = "bazarr-external"


@dataclass
class LedgerEntry:
    id: int
    canonical_path: str
    series_id: int | None
    sonarr_episode_id: int | None
    radarr_movie_id: int | None
    scan_id: str | None
    source: str
    subgen_version: str | None
    queued_at: float
    completed_at: float | None
    bazarr_scan_triggered_at: float | None
    # #451: explicit provenance. Order is fixed (task, source_language,
    # target_language, submission_origin, webhook_*, provenance_conflict).
    # provenance_conflict holds the raw INTEGER tri-state (NULL/0/1);
    # to_dict() exposes it as bool | None per the design contract.
    task: str | None = None
    source_language: str | None = None
    target_language: str | None = None
    submission_origin: str | None = None
    webhook_event: str | None = None
    webhook_language: str | None = None
    webhook_subtitle: str | None = None
    provenance_conflict: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "canonical_path": self.canonical_path,
            "series_id": self.series_id,
            "sonarr_episode_id": self.sonarr_episode_id,
            "radarr_movie_id": self.radarr_movie_id,
            "scan_id": self.scan_id,
            "source": self.source,
            "subgen_version": self.subgen_version,
            "queued_at": self.queued_at,
            "completed_at": self.completed_at,
            "bazarr_scan_triggered_at": self.bazarr_scan_triggered_at,
            "task": self.task,
            "source_language": self.source_language,
            "target_language": self.target_language,
            "submission_origin": self.submission_origin,
            "webhook_event": self.webhook_event,
            "webhook_language": self.webhook_language,
            "webhook_subtitle": self.webhook_subtitle,
            "provenance_conflict": (
                None if self.provenance_conflict is None else bool(self.provenance_conflict)
            ),
        }


# Schema (subs_generated + indexes) is owned by migrations/001_baseline.sql
# (extended by 030_pr451_provenance.sql). run_migrations() runs at boot before
# this store — no per-store init_schema().

# One explicit column list shared by every ledger SELECT so the dataclass
# unpacking and the schema can never drift apart. Only interpolation is this
# module constant (never user input); every runtime value is bound as `?`.
_LEDGER_COLS = (
    "id, canonical_path, series_id, sonarr_episode_id, radarr_movie_id, "
    "scan_id, source, subgen_version, queued_at, completed_at, "
    "bazarr_scan_triggered_at, task, source_language, target_language, "
    "submission_origin, webhook_event, webhook_language, webhook_subtitle, "
    "provenance_conflict"
)
_SELECT_LEDGER = f"SELECT {_LEDGER_COLS} FROM subs_generated"  # nosec B608


# #451 normalized claim comparison: `event=transcribed|translated` is webhook
# TASK evidence, `language` is webhook TARGET-LANGUAGE evidence (never source),
# and `subtitle` is a locator that takes part in NO comparison. "Normalization"
# at the store level is case/whitespace folding plus the transcribe/translate
# ↔ transcribed/translated vocabulary pairing — ISO/alias mapping is the
# checker's job (phase 3), not the store's.
_TASK_NORMS = {
    "transcribe": "transcribe",
    "transcribed": "transcribe",
    "translate": "translate",
    "translated": "translate",
}


def _norm_claim(value: str) -> str:
    return value.strip().casefold()


def _norm_task(value: str) -> str:
    v = _norm_claim(value)
    return _TASK_NORMS.get(v, v)


class ProvenanceStore:
    """Same SQLite file as ScanStore; separate connection so we don't
    contend on the scan store's lock during the completion watcher's
    background polling."""

    def __init__(self, db_path: Path):
        self._path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        apply_journal_mode(self._conn, db_path)
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def record(
        self,
        *,
        canonical_path: str,
        scan_id: str | None,
        source: str = SOURCE_SUBGENSCAN,
        series_id: int | None = None,
        sonarr_episode_id: int | None = None,
        radarr_movie_id: int | None = None,
        subgen_version: str | None = None,
        task: str | None = None,
        source_language: str | None = None,
        target_language: str | None = None,
        submission_origin: str | None = None,
    ) -> int:
        with self._lock:
            # #287: dedup OPEN rows. A re-search of a still-in-flight path
            # (completed_at IS NULL) must reuse the open ledger row — two open
            # rows would both poll to completion and fire _run_aftercare twice
            # for one transcription. Single-process safe under self._lock; the
            # 030 partial-UNIQUE index is the DB-level backstop.
            existing = self._conn.execute(
                "SELECT id FROM subs_generated "
                "WHERE canonical_path = ? AND completed_at IS NULL "
                "ORDER BY id LIMIT 1",
                (canonical_path,),
            ).fetchone()
            if existing is not None:
                return existing[0]
            cur = self._conn.execute(
                "INSERT INTO subs_generated "
                "(canonical_path, series_id, sonarr_episode_id, radarr_movie_id, "
                " scan_id, source, subgen_version, queued_at, "
                " task, source_language, target_language, submission_origin) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    canonical_path,
                    series_id,
                    sonarr_episode_id,
                    radarr_movie_id,
                    scan_id,
                    source,
                    subgen_version,
                    time.time(),
                    task,
                    source_language,
                    target_language,
                    submission_origin,
                ),
            )
            return cur.lastrowid

    def pending(self) -> list[LedgerEntry]:
        """All entries where subgen hasn't reported completion yet.
        Watcher reads this every poll cycle."""
        with self._lock:
            rows = self._conn.execute(
                f"{_SELECT_LEDGER} WHERE completed_at IS NULL"  # nosec B608
            ).fetchall()
        return [LedgerEntry(*r) for r in rows]

    def mark_completed(self, ledger_id: int, when: float | None = None) -> None:
        ts = when if when is not None else time.time()
        with self._lock:
            # #287: stamp the FIRST completion only — a later re-poll of the
            # same path must not overwrite the original completed_at (mirrors
            # complete_by_canonical's `completed_at IS NULL` idempotency).
            self._conn.execute(
                "UPDATE subs_generated SET completed_at = ? WHERE id = ? AND completed_at IS NULL",
                (ts, ledger_id),
            )

    def mark_bazarr_triggered(self, ledger_id: int, when: float | None = None) -> None:
        ts = when if when is not None else time.time()
        with self._lock:
            self._conn.execute(
                "UPDATE subs_generated SET bazarr_scan_triggered_at = ? WHERE id = ?",
                (ts, ledger_id),
            )

    def query_by_path(self, canonical_path: str, limit: int = 20) -> list[LedgerEntry]:
        with self._lock:
            rows = self._conn.execute(
                f"{_SELECT_LEDGER} WHERE canonical_path = ? "  # nosec B608
                "ORDER BY queued_at DESC LIMIT ?",
                (canonical_path, limit),
            ).fetchall()
        return [LedgerEntry(*r) for r in rows]

    def recent(self, limit: int = 50) -> list[LedgerEntry]:
        with self._lock:
            rows = self._conn.execute(
                f"{_SELECT_LEDGER} ORDER BY queued_at DESC LIMIT ?",  # nosec B608
                (limit,),
            ).fetchall()
        return [LedgerEntry(*r) for r in rows]

    def completed_paths_since(self, since_epoch: float) -> set[str]:
        """#229 reconciliation helper: canonical paths whose transcription
        completed (completed_at IS NOT NULL) on or after since_epoch. Used
        by ScanStore.mark_orphaned_before to NOT orphan paths that subgen
        actually finished before the restart."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT canonical_path FROM subs_generated "
                "WHERE completed_at IS NOT NULL AND completed_at >= ?",
                (since_epoch,),
            ).fetchall()
        return {r[0] for r in rows}

    def completed_without_bazarr(self, max_age_s: int = 86400) -> list[LedgerEntry]:
        """Entries that completed transcribe but never fired Bazarr's
        scan-disk task. Retry fodder for the completion watcher.

        `max_age_s` bounds how far back we'll look — avoids triggering a
        forever-old retry storm after a long subarr downtime."""
        import time

        cutoff = time.time() - max_age_s
        with self._lock:
            rows = self._conn.execute(
                f"{_SELECT_LEDGER} "  # nosec B608
                "WHERE completed_at IS NOT NULL "
                "  AND bazarr_scan_triggered_at IS NULL "
                "  AND series_id IS NOT NULL "
                "  AND completed_at >= ? "
                "ORDER BY completed_at DESC",
                (cutoff,),
            ).fetchall()
        return [LedgerEntry(*r) for r in rows]

    def record_webhook_and_complete(
        self,
        *,
        canonical_path: str,
        event: str | None = None,
        language: str | None = None,
        subtitle: str | None = None,
        received_at: float | None = None,
    ) -> int:
        """#451 atomic webhook completion: persist webhook evidence and complete
        the open ledger row in one transaction. `event` (transcribed|translated)
        maps only to webhook TASK evidence; `language` maps only to webhook
        TARGET-LANGUAGE evidence; `subtitle` is a canonicalized output-path
        locator and never a language claim. Semantics (all idempotent / exactly
        once): BEGIN IMMEDIATE so concurrent deliveries serialize; select the
        LOWEST-id OPEN (completed_at IS NULL) row for the path; write each
        webhook column only when NULL (first delivery wins, identical values are
        no-ops so repeated/polling deliveries never clobber evidence); compare
        NORMALIZED submission claims (task, target_language) against webhook
        claims (event -> task, language -> target, NULL never compared) and set
        sticky provenance_conflict=1 on disagreement / 0 on agreement / keep NULL
        when nothing comparable; set completed_at exactly once; COMMIT. Returns
        the completed ledger id, or 0 when no open row matched (never tracked /
        already completed -> idempotent no-op)."""
        ts = received_at if received_at is not None else time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT id, task, target_language, webhook_event, webhook_language "
                    "FROM subs_generated WHERE canonical_path = ? AND completed_at IS NULL "
                    "ORDER BY id LIMIT 1",
                    (canonical_path,),
                ).fetchone()
                if row is None:
                    # No open ledger row: never recorded, already completed by
                    # another delivery / the polling watcher, or a double fire.
                    self._conn.execute("COMMIT")
                    return 0
                ledger_id, sub_task, sub_target, stored_event, stored_lang = row

                # NULL-only evidence writes: first writer wins.
                self._conn.execute(
                    "UPDATE subs_generated SET "
                    " webhook_event = COALESCE(webhook_event, ?), "
                    " webhook_language = COALESCE(webhook_language, ?), "
                    " webhook_subtitle = COALESCE(webhook_subtitle, ?) "
                    "WHERE id = ?",
                    (event, language, subtitle, ledger_id),
                )

                # Effective claims = the first-writer (stored) values.
                eff_event = stored_event if stored_event is not None else event
                eff_lang = stored_lang if stored_lang is not None else language
                new_conflict = self._compare_claims(
                    submission_task=sub_task,
                    submission_target=sub_target,
                    webhook_event=eff_event,
                    webhook_language=eff_lang,
                )
                if new_conflict is not None:
                    # Sticky: an existing 1 (conflict) is never cleared. The
                    # guard must also match NULL (NULL != 1 is UNKNOWN in SQL),
                    # so a fresh row with no prior conflict still gets set.
                    self._conn.execute(
                        "UPDATE subs_generated SET provenance_conflict = ? "
                        "WHERE id = ? AND (provenance_conflict IS NULL OR provenance_conflict != 1)",
                        (new_conflict, ledger_id),
                    )

                # Exactly-once completion.
                self._conn.execute(
                    "UPDATE subs_generated SET completed_at = ? WHERE id = ? AND completed_at IS NULL",
                    (ts, ledger_id),
                )
                self._conn.execute("COMMIT")
                return ledger_id
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass  # transaction already aborted by sqlite itself
                raise

    @staticmethod
    def _compare_claims(
        submission_task: str | None,
        submission_target: str | None,
        webhook_event: str | None,
        webhook_language: str | None,
    ) -> int | None:
        """#451 normalized submission-vs-webhook claim comparison. Returns the
        provenance_conflict tri-state for THIS comparison: None = no attribute
        has BOTH a submission and a webhook claim (evidence never compared, keep
        NULL); 0 = compared and no disagreement; 1 = at least one attribute
        disagrees (the caller makes it sticky)."""
        compared = False
        disagree = False
        if submission_task is not None and webhook_event is not None:
            compared = True
            if _norm_task(submission_task) != _norm_task(webhook_event):
                disagree = True
        if submission_target is not None and webhook_language is not None:
            compared = True
            if _norm_claim(submission_target) != _norm_claim(webhook_language):
                disagree = True
        if not compared:
            return None
        return 1 if disagree else 0
