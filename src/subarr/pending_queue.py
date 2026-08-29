"""#66 / #116: subarr's pending-queue store — the queue authority in front of
subgen.

Producers (manual / gaps / backfill / auto) enqueue jobs here; a depth-aware
feeder (slice 2) drains them into subgen one at a time via single-file `/batch`.
Pending rows are fully subarr-controlled (reorder / promote / demote / remove);
once submitted to subgen they're cancel-only.

Ordering for the feeder is **(priority DESC, position ASC)** — per-source
priority buckets group jobs (manual > gaps/auto > backfill), and `position`
(a REAL) orders within a bucket so reordering is O(1) via midpoints. Dragging a
job next to one in another bucket adopts that bucket's priority.

Single connection + Lock + WAL, mirroring the other stores. All methods are
best-effort about concurrency via the Lock; they raise on programmer error
(unknown id) where a caller would want to know.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .data_persistence import apply_journal_mode

# ── statuses ────────────────────────────────────────────────────────
STATUS_PENDING = "pending"  # in subarr's queue, not yet sent to subgen
STATUS_SUBMITTED = "submitted"  # handed to subgen; cancel-only from here
STATUS_DONE = "done"
STATUS_ERROR = "error"

ACTIVE_STATUSES = (STATUS_PENDING, STATUS_SUBMITTED)

# ── per-source priority buckets (higher drains first) ───────────────
PRIORITY_BY_SOURCE = {
    "manual": 2,  # user clicked transcribe — most urgent
    "gaps": 1,  # coverage gap queue
    "auto": 1,  # scheduler auto-queue
    "backfill": 0,  # bulk library backfill (#116) — lowest
}
DEFAULT_PRIORITY = 0


@dataclass
class PendingJob:
    id: str
    canonical_path: str
    position: float
    priority: int
    status: str
    audio_language_override: str | None
    task: str | None
    source: str
    created_at: float
    submitted_at: float | None
    error: str | None
    # #66/#116 slice 6: carried so the feeder can write full provenance
    # (completion_watcher needs series_id to fire Bazarr's scan-disk task).
    series_id: int | None = None
    sonarr_episode_id: int | None = None
    # #317 Slice B: forward ?ignore_forced=true to subgen's /batch for THIS job
    # so a forced-only file is transcribed instead of skipped ("transcribe a
    # full sub anyway"). Default False = normal skip behaviour.
    ignore_forced: bool = False
    # [#458 follow-on] forward ?bypass_skip=true to subgen's /batch for THIS
    # job, overruling every skip heuristic. For the image-only-subtitle class,
    # which SKIP_IF_AUDIO_LANGUAGES otherwise refuses forever.
    bypass_skip: bool = False
    # #368: owning movie's Radarr id, carried so completion_watcher can upload the
    # finished .srt straight to the owning Bazarr (the movie analogue of
    # series_id + sonarr_episode_id). None for episodes / path-only jobs.
    radarr_movie_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "canonical_path": self.canonical_path,
            "position": self.position,
            "priority": self.priority,
            "status": self.status,
            "audio_language_override": self.audio_language_override,
            "task": self.task,
            "source": self.source,
            "created_at": self.created_at,
            "submitted_at": self.submitted_at,
            "error": self.error,
            "series_id": self.series_id,
            "sonarr_episode_id": self.sonarr_episode_id,
            "ignore_forced": self.ignore_forced,
            "bypass_skip": self.bypass_skip,
            "radarr_movie_id": self.radarr_movie_id,
        }


_COLS = (
    "id, canonical_path, position, priority, status, "
    "audio_language_override, task, source, created_at, submitted_at, error, "
    "series_id, sonarr_episode_id, ignore_forced, radarr_movie_id, bypass_skip"
)

# Statements built ONCE as constants. The only interpolation is the static
# `_COLS` column list (a module constant, never user input); every runtime value
# is bound as a `?` parameter. Hoisting them here means the execute() call sites
# pass a constant name — no string formatting at the call — which is both
# cleaner and keeps bandit's B608 (string-built SQL) quiet by construction. The
# two statements whose literal text contains a SQL keyword (SELECT.../INSERT...)
# carry an explicit nosec; the rest interpolate `_SELECT` so they have no literal
# keyword to match.
_SELECT = f"SELECT {_COLS} FROM pending_queue"  # nosec B608
_Q_ACTIVE_BY_PATH = f"{_SELECT} WHERE canonical_path = ? AND status IN (?, ?) LIMIT 1"
_Q_MAXPOS = "SELECT MAX(position) FROM pending_queue WHERE status = ? AND priority = ?"
_Q_INSERT = (  # nosec B608
    # Placeholders are DERIVED from _COLS rather than written out. They were a
    # hardcoded run of 15 "?", which silently went out of step with _COLS the
    # moment a column was added (#458 follow-on added the 16th). Deriving them
    # makes that class of drift impossible. Still no user input in the SQL:
    # _COLS is a module constant and every value is bound as a parameter.
    f"INSERT INTO pending_queue ({_COLS}) VALUES ({', '.join('?' * (_COLS.count(',') + 1))})"
)
_Q_GET = f"{_SELECT} WHERE id = ?"
_Q_LIST_ALL = f"{_SELECT} ORDER BY priority DESC, position ASC, created_at ASC"
_Q_LIST_STATUS = f"{_SELECT} WHERE status = ? ORDER BY priority DESC, position ASC, created_at ASC"
_Q_NEXT = f"{_Q_LIST_STATUS} LIMIT ?"
_Q_BUCKET = f"{_SELECT} WHERE status = ? AND priority = ? AND id != ? ORDER BY position ASC, created_at ASC"
_Q_MINMAX = (
    "SELECT MIN(position), MAX(position) FROM pending_queue WHERE status = ? AND priority = ? AND id != ?"
)


def _row(r: tuple) -> PendingJob:
    return PendingJob(
        id=r[0],
        canonical_path=r[1],
        position=r[2],
        priority=r[3],
        status=r[4],
        audio_language_override=r[5],
        task=r[6],
        source=r[7],
        created_at=r[8],
        submitted_at=r[9],
        error=r[10],
        series_id=r[11],
        sonarr_episode_id=r[12],
        ignore_forced=bool(r[13]),
        bypass_skip=bool(r[15]),
        radarr_movie_id=r[14],
    )


class PendingQueueStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
            isolation_level=None,
        )
        apply_journal_mode(self._conn, db_path)
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── enqueue (with dedup) ────────────────────────────────────────

    def enqueue(
        self,
        canonical_path: str,
        *,
        source: str = "manual",
        priority: int | None = None,
        audio_language_override: str | None = None,
        task: str | None = None,
        series_id: int | None = None,
        sonarr_episode_id: int | None = None,
        ignore_forced: bool = False,
        bypass_skip: bool = False,
        radarr_movie_id: int | None = None,
    ) -> PendingJob:
        """Add a job, or return the existing ACTIVE one for this path (dedup —
        we never double-queue a file that's already pending or in subgen).
        New jobs append to the end of their priority bucket."""
        prio = priority if priority is not None else PRIORITY_BY_SOURCE.get(source, DEFAULT_PRIORITY)
        with self._lock:
            existing = self._conn.execute(
                _Q_ACTIVE_BY_PATH,
                (canonical_path, STATUS_PENDING, STATUS_SUBMITTED),
            ).fetchone()
            if existing:
                # Dedup wins: an already-pending/submitted path returns the
                # existing job unchanged — including its ignore_forced. So
                # "transcribe anyway" on a file already queued as a normal gap
                # won't retro-set the override. Harmless in practice: forced-
                # only rows are never auto-enqueued (the scheduler treats them
                # as non-gaps), so this collision effectively can't arise.
                return _row(existing)
            # append within the bucket: max position among pending of this prio
            row = self._conn.execute(
                _Q_MAXPOS,
                (STATUS_PENDING, prio),
            ).fetchone()
            pos = (row[0] + 1.0) if row and row[0] is not None else 0.0
            job = PendingJob(
                id=uuid.uuid4().hex,
                canonical_path=canonical_path,
                position=pos,
                priority=prio,
                status=STATUS_PENDING,
                audio_language_override=audio_language_override,
                task=task,
                source=source,
                created_at=time.time(),
                submitted_at=None,
                error=None,
                series_id=series_id,
                sonarr_episode_id=sonarr_episode_id,
                ignore_forced=ignore_forced,
                bypass_skip=bypass_skip,
                radarr_movie_id=radarr_movie_id,
            )
            self._conn.execute(
                _Q_INSERT,
                (
                    job.id,
                    job.canonical_path,
                    job.position,
                    job.priority,
                    job.status,
                    job.audio_language_override,
                    job.task,
                    job.source,
                    job.created_at,
                    job.submitted_at,
                    job.error,
                    job.series_id,
                    job.sonarr_episode_id,
                    int(job.ignore_forced),
                    job.radarr_movie_id,
                    int(job.bypass_skip),
                ),
            )
            return job

    # ── reads ───────────────────────────────────────────────────────

    def get(self, job_id: str) -> PendingJob | None:
        with self._lock:
            r = self._conn.execute(
                _Q_GET,
                (job_id,),
            ).fetchone()
        return _row(r) if r else None

    def list(self, status: str | None = None) -> list[PendingJob]:
        """All jobs (optionally filtered by status) in feeder order:
        priority DESC, position ASC, then created_at ASC as a stable tiebreak."""
        with self._lock:
            if status is None:
                rows = self._conn.execute(_Q_LIST_ALL).fetchall()
            else:
                rows = self._conn.execute(_Q_LIST_STATUS, (status,)).fetchall()
        return [_row(r) for r in rows]

    def next_pending(self, limit: int = 1) -> list[PendingJob]:
        """The next N jobs the feeder should submit (highest priority first)."""
        with self._lock:
            rows = self._conn.execute(
                _Q_NEXT,
                (STATUS_PENDING, max(1, limit)),
            ).fetchall()
        return [_row(r) for r in rows]

    def active_paths(self) -> set[str]:
        """Canonical paths currently pending or submitted — used by producers
        (scheduler/coverage) to avoid re-queuing what's already in flight in
        the pending queue (provenance only covers already-submitted jobs)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT canonical_path FROM pending_queue WHERE status IN (?, ?)",
                (STATUS_PENDING, STATUS_SUBMITTED),
            ).fetchall()
        return {r[0] for r in rows}

    def count_by_status(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute("SELECT status, COUNT(*) FROM pending_queue GROUP BY status").fetchall()
        return {r[0]: r[1] for r in rows}

    # ── status transitions ──────────────────────────────────────────

    def set_status(
        self, job_id: str, status: str, *, error: str | None = None, submitted_at: float | None = None
    ) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE pending_queue SET status = ?, error = ?, "
                "submitted_at = COALESCE(?, submitted_at) WHERE id = ?",
                (status, error, submitted_at, job_id),
            )
            return cur.rowcount > 0

    def mark_submitted(self, job_id: str) -> bool:
        return self.set_status(job_id, STATUS_SUBMITTED, submitted_at=time.time())

    def remove(self, job_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM pending_queue WHERE id = ?",
                (job_id,),
            )
            return cur.rowcount > 0

    def resolve_skipped(self, canonical_path: str) -> int:
        """[#468] REMOVE SUBMITTED rows for a path subgen told us it skipped.

        Same disposal as resolve_orphaned_submitted -- the scan has recorded the
        outcome, so the pending row has done its job -- but driven by subgen's
        explicit answer instead of by the 60s absence timeout. subgen reports
        `skipped` in the POST /batch response within milliseconds; waiting out
        ORPHAN_GRACE_S to infer the same thing is what left @AztecGuyGDL with
        110 stale rows draining slowly.

        Deliberately only SUBMITTED. A PENDING row for the same path has not
        been sent to subgen yet, so this verdict says nothing about it, and
        dropping it would silently discard work the user is still waiting on.
        """
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM pending_queue WHERE status = ? AND canonical_path = ?",
                (STATUS_SUBMITTED, canonical_path),
            )
            return cur.rowcount or 0

    def resolve_orphaned_submitted(self, live_subgen_paths: set[str], *, older_than_s: float) -> int:
        """#336: REMOVE SUBMITTED jobs that subgen no longer reports and that were
        submitted longer ago than `older_than_s` (past the surface-in-/queue grace).

        A submitted job leaves subgen's queue when it's transcribed, SKIPPED (sub
        already exists / 404 file gone), or lost on a subgen restart — in every
        case the scan already recorded the outcome in history, so the pending row
        has done its job and is dropped. This replaces an earlier reconcile that
        RE-PENDED orphans (flipped them back to PENDING), which re-submitted
        already-done files forever — the zombie churn that piled up orphaned
        SUBMITTED rows and starved the feeder's effective depth. A genuinely lost
        transcription re-surfaces as a coverage gap on the next walk. Returns the
        number removed."""
        from .paths import canonical_to_subgen_batch

        cutoff = time.time() - older_than_s
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, canonical_path, submitted_at FROM pending_queue WHERE status = ?",
                (STATUS_SUBMITTED,),
            ).fetchall()
            removed = 0
            for job_id, canonical_path, submitted_at in rows:
                if submitted_at is not None and submitted_at > cutoff:
                    continue  # within grace — subgen may not have surfaced it yet
                if canonical_to_subgen_batch(canonical_path) not in live_subgen_paths:
                    self._conn.execute("DELETE FROM pending_queue WHERE id = ?", (job_id,))
                    removed += 1
            return removed

    def clear(self, status: str | None = None) -> int:
        with self._lock:
            if status is None:
                cur = self._conn.execute("DELETE FROM pending_queue")
            else:
                cur = self._conn.execute(
                    "DELETE FROM pending_queue WHERE status = ?",
                    (status,),
                )
            return cur.rowcount

    # ── reorder (pending only) ──────────────────────────────────────

    def promote(self, job_id: str) -> PendingJob:
        """Move to the TOP of its priority bucket."""
        return self._reposition(job_id, to_top=True)

    def demote(self, job_id: str) -> PendingJob:
        """Move to the BOTTOM of its priority bucket."""
        return self._reposition(job_id, to_top=False)

    def _reposition(self, job_id: str, *, to_top: bool) -> PendingJob:
        with self._lock:
            job = self._require(job_id)
            row = self._conn.execute(
                _Q_MINMAX,
                (STATUS_PENDING, job.priority, job_id),
            ).fetchone()
            lo, hi = row or (None, None)
            if lo is None:
                new_pos = job.position  # only item in its bucket — no-op
            else:
                new_pos = (lo - 1.0) if to_top else (hi + 1.0)
            self._conn.execute(
                "UPDATE pending_queue SET position = ? WHERE id = ?",
                (new_pos, job_id),
            )
            return self._require(job_id)

    def move(self, job_id: str, *, before_id: str | None = None, after_id: str | None = None) -> PendingJob:
        """Place `job_id` immediately before/after a target job. Adopts the
        target's priority bucket (so dragging across buckets re-prioritises),
        and takes the midpoint position between the target and its neighbour on
        that side."""
        if not (before_id or after_id):
            raise ValueError("move() needs before_id or after_id")
        target_id = before_id or after_id
        with self._lock:
            self._require(job_id)
            target = self._require(target_id)
            # ordered pending of the target's bucket, excluding the moving job
            ordered = [
                _row(r)
                for r in self._conn.execute(
                    _Q_BUCKET,
                    (STATUS_PENDING, target.priority, job_id),
                ).fetchall()
            ]
            idx = next((i for i, j in enumerate(ordered) if j.id == target_id), None)
            if idx is None:
                # target not pending in its bucket — fall back to appending
                new_pos = (ordered[-1].position + 1.0) if ordered else target.position
            elif before_id:
                prev = ordered[idx - 1].position if idx > 0 else (target.position - 2.0)
                new_pos = (prev + target.position) / 2.0
            else:  # after_id
                nxt = ordered[idx + 1].position if idx + 1 < len(ordered) else (target.position + 2.0)
                new_pos = (target.position + nxt) / 2.0
            self._conn.execute(
                "UPDATE pending_queue SET priority = ?, position = ? WHERE id = ?",
                (target.priority, new_pos, job_id),
            )
            return self._require(job_id)

    def move_step(self, job_id: str, *, up: bool) -> PendingJob:
        """Move a job one step up/down in the FULL pending order (across priority
        buckets). The neighbour is resolved HERE from current state, not handed in
        by the caller — so reorder is immune to a stale client view (the feeder
        churns the list constantly + the UI only polls every few seconds). At a
        boundary it's a no-op. Reuses move() for the actual placement."""
        ordered = self.list(status=STATUS_PENDING)
        idx = next((i for i, j in enumerate(ordered) if j.id == job_id), None)
        if idx is None:
            raise KeyError(job_id)
        if up:
            if idx == 0:
                return ordered[idx]  # already at the top
            return self.move(job_id, before_id=ordered[idx - 1].id)
        if idx >= len(ordered) - 1:
            return ordered[idx]  # already at the bottom
        return self.move(job_id, after_id=ordered[idx + 1].id)

    # ── internal ────────────────────────────────────────────────────

    def _require(self, job_id: str) -> PendingJob:
        r = self._conn.execute(
            _Q_GET,
            (job_id,),
        ).fetchone()
        if r is None:
            raise KeyError(f"pending job {job_id!r} not found")
        return _row(r)
