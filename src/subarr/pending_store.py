"""Pending-approval store for manual_confirm scheduler mode.

When the scheduler fires in MODE_MANUAL_CONFIRM, instead of enqueueing the
`queue`-marked decisions it stores them here for the user to review.
The user then approves all or a subset; approved decisions go through
the same `_enqueue` path the auto_rules flow uses.

Two tables:
- pending_walks: one row per scheduler walk that emitted a pending list.
  Holds summary stats + a status (pending / approved_all / approved_partial
  / rejected / expired).
- pending_decisions: one row per CoverageItem that survived the rules
  filter, joined to a walk via walk_id. `approved` is null until the
  user acts on it.

Auto-expire: walks older than `EXPIRE_AFTER_S` (7 days) get garbage-
collected on next walk creation. Prevents stale lists piling up.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .data_persistence import apply_journal_mode


STATUS_PENDING = "pending"
STATUS_APPROVED_ALL = "approved_all"
STATUS_APPROVED_PARTIAL = "approved_partial"
STATUS_REJECTED = "rejected"
STATUS_EXPIRED = "expired"

EXPIRE_AFTER_S = 7 * 86400


@dataclass
class PendingDecision:
    id: int
    walk_id: str
    item: dict[str, Any]
    approved: bool | None
    scan_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "walk_id": self.walk_id,
            "item": self.item,
            "approved": self.approved,
            "scan_id": self.scan_id,
        }


@dataclass
class PendingWalk:
    id: str
    created_at: float
    status: str
    considered: int
    decisions_total: int
    approved_count: int
    decisions: list[PendingDecision] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "status": self.status,
            "considered": self.considered,
            "decisions_total": self.decisions_total,
            "approved_count": self.approved_count,
            "decisions": [d.to_dict() for d in self.decisions],
        }


# Schema (pending_walks + pending_decisions + indexes) is owned by
# migrations/001_baseline.sql. run_migrations() runs at boot before this
# store — no per-store init_schema().


class PendingStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        apply_journal_mode(self._conn, db_path)
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def prune(self, days: int = 30) -> int:
        """#197: resolved/expired manual-confirm walks are history once
        decided; their decisions cascade with them (FK). PROTECTIVE RULE:
        status='pending' walks are the user's open approval queue — never
        pruned, regardless of age. Run on boot."""
        import time

        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM pending_walks WHERE created_at < ? AND status != ?",
                (time.time() - days * 86400, STATUS_PENDING),
            )
            return cur.rowcount

    # ── create ────────────────────────────────────────────────────────

    def create_walk(self, *, considered: int, items: list[dict]) -> PendingWalk:
        self._expire_old()
        walk_id = uuid.uuid4().hex[:16]
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO pending_walks (id, created_at, status, considered, decisions_total) "
                "VALUES (?, ?, ?, ?, ?)",
                (walk_id, now, STATUS_PENDING, considered, len(items)),
            )
            for it in items:
                self._conn.execute(
                    "INSERT INTO pending_decisions (walk_id, item_json) VALUES (?, ?)",
                    (walk_id, json.dumps(it)),
                )
        return PendingWalk(
            id=walk_id,
            created_at=now,
            status=STATUS_PENDING,
            considered=considered,
            decisions_total=len(items),
            approved_count=0,
            decisions=self._list_decisions(walk_id),
        )

    def _expire_old(self) -> None:
        cutoff = time.time() - EXPIRE_AFTER_S
        with self._lock:
            self._conn.execute(
                "UPDATE pending_walks SET status = ? WHERE status = ? AND created_at < ?",
                (STATUS_EXPIRED, STATUS_PENDING, cutoff),
            )

    # ── read ──────────────────────────────────────────────────────────

    def get_walk(self, walk_id: str) -> PendingWalk | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, created_at, status, considered, decisions_total, approved_count "
                "FROM pending_walks WHERE id = ?",
                (walk_id,),
            ).fetchone()
        if not row:
            return None
        return PendingWalk(
            id=row[0],
            created_at=row[1],
            status=row[2],
            considered=row[3],
            decisions_total=row[4],
            approved_count=row[5],
            decisions=self._list_decisions(walk_id),
        )

    def list_walks(self, *, status: str | None = None, limit: int = 50) -> list[PendingWalk]:
        with self._lock:
            if status:
                rows = self._conn.execute(
                    "SELECT id, created_at, status, considered, decisions_total, approved_count "
                    "FROM pending_walks WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id, created_at, status, considered, decisions_total, approved_count "
                    "FROM pending_walks ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [
            PendingWalk(
                id=r[0],
                created_at=r[1],
                status=r[2],
                considered=r[3],
                decisions_total=r[4],
                approved_count=r[5],
            )
            for r in rows
        ]

    def pending_count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM pending_walks WHERE status = ?",
                (STATUS_PENDING,),
            ).fetchone()
        return int(row[0]) if row else 0

    def _list_decisions(self, walk_id: str) -> list[PendingDecision]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, walk_id, item_json, approved, scan_id "
                "FROM pending_decisions WHERE walk_id = ? ORDER BY id",
                (walk_id,),
            ).fetchall()
        out: list[PendingDecision] = []
        for r in rows:
            try:
                item = json.loads(r[2])
            except (ValueError, TypeError):
                item = {}
            out.append(
                PendingDecision(
                    id=r[0],
                    walk_id=r[1],
                    item=item,
                    approved=None if r[3] is None else bool(r[3]),
                    scan_id=r[4],
                )
            )
        return out

    # ── decide ────────────────────────────────────────────────────────

    def mark_decision(self, decision_id: int, *, approved: bool, scan_id: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE pending_decisions SET approved = ?, scan_id = ? WHERE id = ?",
                (1 if approved else 0, scan_id, decision_id),
            )

    def finalise_walk(self, walk_id: str) -> str:
        """Recompute walk status from its decisions and persist it.
        Returns the new status."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT approved FROM pending_decisions WHERE walk_id = ?",
                (walk_id,),
            ).fetchall()
            if not rows:
                return STATUS_PENDING
            approved = sum(1 for r in rows if r[0] == 1)
            rejected = sum(1 for r in rows if r[0] == 0)
            total = len(rows)
            if approved == total:
                new_status = STATUS_APPROVED_ALL
            elif rejected == total:
                new_status = STATUS_REJECTED
            elif approved + rejected == total:
                new_status = STATUS_APPROVED_PARTIAL
            else:
                new_status = STATUS_PENDING  # still some undecided
            self._conn.execute(
                "UPDATE pending_walks SET status = ?, approved_count = ? WHERE id = ?",
                (new_status, approved, walk_id),
            )
        return new_status
