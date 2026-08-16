"""#156 Track A: persistence for per-job aftercare results. One row per
completed job; latest-per-path surfaced. Mirrors error_store/task_health
(single connection, WAL, lock)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .aftercare import AftercareEvaluation
from .data_persistence import apply_journal_mode

# Latest-per-path: the most recently INSERTED row for a path (monotonic id
# avoids the equal-timestamp ambiguity MAX(completed_at) had). The full query
# strings are assembled here at MODULE scope — not via f-strings at the
# execute() call site — so bandit doesn't read them as dynamic SQL (B608).
# They contain only fixed literals + bound ? params; no user input is ever
# interpolated (view selects between two constants; limit/offset/search are
# bound params). The query fragments below are fixed SQL; all runtime values
# remain bound parameters.
_LATEST = "a.id = (SELECT MAX(b.id) FROM aftercare_results b WHERE b.canonical_path = a.canonical_path)"
_PENDING_COUNT_SQL = (
    f"SELECT COUNT(*) FROM aftercare_results a WHERE a.flagged = 1 AND a.reviewed_at IS NULL AND {_LATEST}"
)

# View fragment: `flagged` shows only pending (unreviewed) flagged rows; `all`
# shows every latest-per-path row (flagged or clean, reviewed or not) — just
# `_LATEST` on its own.
_VIEW_FLAGGED = f"{_LATEST} AND a.flagged = 1 AND a.reviewed_at IS NULL"

# Shared, case-insensitive search predicate over the searchable persisted row
# fields (canonical path, source, preview). Three bound ? params in this exact
# order (one per column) — the pattern values come from _like_patterns(), so
# user input is bound and never interpolated into the SQL string. Reused by
# BOTH the page query and the count query so their results always agree.
_SEARCH = (
    "LOWER(a.canonical_path) LIKE ? ESCAPE '\\' "
    "OR LOWER(COALESCE(a.source, '')) LIKE ? ESCAPE '\\' "
    "OR LOWER(COALESCE(a.preview, '')) LIKE ? ESCAPE '\\'"
)
_SEARCH_WHERE = f"AND ({_SEARCH})"
_SOURCE_WHERE = "AND a.source = ?"

_ORDER_BY = "ORDER BY a.flagged DESC, a.completed_at DESC, a.id DESC"
_LIMIT_OFFSET = "LIMIT ? OFFSET ?"

# Page + count SELECTs. `WHERE`/`ORDER BY`/`LIMIT` fragments are concatenated at
# execute time from these module-scope constants only (no user input spliced).
_SELECT_ALL = f"SELECT * FROM aftercare_results a WHERE {_LATEST}"
_SELECT_FLAGGED = f"SELECT * FROM aftercare_results a WHERE {_VIEW_FLAGGED}"
_COUNT_ALL = f"SELECT COUNT(*) FROM aftercare_results a WHERE {_LATEST}"
_COUNT_FLAGGED = f"SELECT COUNT(*) FROM aftercare_results a WHERE {_VIEW_FLAGGED}"


def _like_patterns(term: str) -> list[str]:
    """Return the bound LIKE patterns for the shared _SEARCH predicate, one per
    searched column in the same order. Escapes the LIKE wildcards (\\, %, _) so a
    literal '%' or '_' in the user's query matches literally; the values are
    bound parameters, never spliced into the SQL string."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    return [pattern, pattern, pattern]


class AfterCareStore:
    def __init__(self, db_path: Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        apply_journal_mode(self._conn, db_path)
        self._lock = threading.Lock()

    def prune(self, days: int = 365) -> int:
        """#197: one row per completed job, otherwise forever. A year keeps
        the #95 passive-tuning signal window intact while bounding growth.
        PROTECTIVE RULE: flagged-but-unreviewed rows are the user's review
        backlog — never pruned, regardless of age. Run on boot."""
        import time

        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM aftercare_results WHERE completed_at < ? "
                "AND NOT (flagged = 1 AND reviewed_at IS NULL)",
                (time.time() - days * 86400,),
            )
            return cur.rowcount

    def record(
        self,
        *,
        canonical_path: str,
        completed_at: float,
        evaluation: AftercareEvaluation,
        source: str | None,
        preview: str | None = None,
    ) -> None:
        # `preview` (#216) is an already-sanitized snippet of a representative
        # cue, shown in the review UI to explain why an EXTERNAL sub is flagged.
        # NULL for our own (clean) Whisper output.
        with self._lock:
            self._conn.execute(
                "INSERT INTO aftercare_results "
                "(canonical_path, completed_at, composite, cue_count, flagged, "
                " readability_json, signals_json, source, preview, reviewed_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                (
                    canonical_path,
                    completed_at,
                    evaluation.composite,
                    evaluation.cue_count,
                    1 if evaluation.flagged else 0,
                    json.dumps(evaluation.readability) if evaluation.readability else None,
                    json.dumps(evaluation.signals) if evaluation.signals else None,
                    source,
                    preview,
                    time.time(),
                ),
            )

    def pending_count(self) -> int:
        with self._lock:
            row = self._conn.execute(_PENDING_COUNT_SQL).fetchone()
        return int(row[0])

    def list_results(
        self,
        *,
        view: str,
        limit: int,
        offset: int,
        source: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        """Latest-per-path page of results. `view` ('flagged'|'all'), `source`,
        and `search` filter the set; the ORDER BY/LIMIT/OFFSET fragments are
        appended so the LIMIT/OFFSET bound params stay last. The WHERE/order
        SQL text comes only from module-scope constants — user input (source,
        search, limit, offset) is bound."""
        base = _SELECT_FLAGGED if view == "flagged" else _SELECT_ALL
        fragments = [base]
        params: list[Any] = []
        if search:
            fragments.append(_SEARCH_WHERE)
            params.extend(_like_patterns(search))
        if source is not None:
            fragments.append(_SOURCE_WHERE)
            params.append(source)
        fragments.append(_ORDER_BY)
        fragments.append(_LIMIT_OFFSET)
        sql = " ".join(fragments)
        params.extend([limit, offset])
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def count_results(
        self,
        *,
        view: str,
        source: str | None = None,
        search: str | None = None,
    ) -> int:
        """Total matching rows for `list_results` with the SAME view/source/search
        predicates (and the same bound-parameter order), before pagination — so
        the UI's count is the truthful total, not the returned page length."""
        base = _COUNT_FLAGGED if view == "flagged" else _COUNT_ALL
        fragments = [base]
        params: list[Any] = []
        if search:
            fragments.append(_SEARCH_WHERE)
            params.extend(_like_patterns(search))
        if source is not None:
            fragments.append(_SOURCE_WHERE)
            params.append(source)
        sql = " ".join(fragments)
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return int(row[0])

    def get(self, result_id: int) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute(
                "SELECT * FROM aftercare_results WHERE id = ?",
                (result_id,),
            ).fetchone()
        return self._row_to_dict(r) if r else None

    def mark_reviewed(self, result_id: int) -> bool:
        """Mark a result reviewed. Idempotent: returns True if the row exists
        (re-acking a reviewed row is a no-op that still returns True); returns
        False only when no such id exists. Preserves the original review time."""
        with self._lock:
            row = self._conn.execute(
                "SELECT reviewed_at FROM aftercare_results WHERE id = ?",
                (result_id,),
            ).fetchone()
            if row is None:
                return False
            if row["reviewed_at"] is None:
                self._conn.execute(
                    "UPDATE aftercare_results SET reviewed_at = ? WHERE id = ?",
                    (time.time(), result_id),
                )
            return True

    def mark_all_reviewed(self, *, source: str | None = None) -> int:
        """#313: mark every still-pending result reviewed in one action — the
        bulk-acknowledge for a large first-run backlog (e.g. a 4500-episode
        library audited on day one). Optional source filter mirrors
        list_results ('existing_audit' etc.). Only touches rows where
        reviewed_at IS NULL, so already-reviewed rows keep their original time.
        Returns the count marked."""
        now = time.time()
        with self._lock:
            if source is not None:
                cur = self._conn.execute(
                    "UPDATE aftercare_results SET reviewed_at = ? WHERE reviewed_at IS NULL AND source = ?",
                    (now, source),
                )
            else:
                cur = self._conn.execute(
                    "UPDATE aftercare_results SET reviewed_at = ? WHERE reviewed_at IS NULL",
                    (now,),
                )
            return cur.rowcount or 0

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row_to_dict(r: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": r["id"],
            "canonical_path": r["canonical_path"],
            "completed_at": r["completed_at"],
            "composite": r["composite"],
            "cue_count": r["cue_count"],
            "flagged": bool(r["flagged"]),
            "readability": json.loads(r["readability_json"]) if r["readability_json"] else None,
            "signals": json.loads(r["signals_json"]) if r["signals_json"] else None,
            "source": r["source"],
            "preview": r["preview"],
            "reviewed_at": r["reviewed_at"],
            "created_at": r["created_at"],
        }
