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
        }


_COLS = (
    "id, canonical_path, position, priority, status, "
    "audio_language_override, task, source, created_at, submitted_at, error, "
    "series_id, sonarr_episode_id"
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
    f"INSERT INTO pending_queue ({_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
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
    )


class PendingQueueStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
            isolation_level=None,
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
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

    # ── internal ────────────────────────────────────────────────────

    def _require(self, job_id: str) -> PendingJob:
        r = self._conn.execute(
            _Q_GET,
            (job_id,),
        ).fetchone()
        if r is None:
            raise KeyError(f"pending job {job_id!r} not found")
        return _row(r)
