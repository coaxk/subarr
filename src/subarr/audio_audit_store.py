"""#155 phase 2 — SQLite store for the library-wide audio-language audit.

One row per file (keyed by canonical_path), holding the resolved verdict from
`resolve_source_language` plus the raw per-chunk evidence. The `mtime` makes the
walk RESUMABLE: a re-run skips a file whose stored mtime still matches on disk.

Schema (audio_lang_audit) is owned by migrations/010_audio_audit.sql.
run_migrations() runs at boot before this store is constructed — no per-store
init_schema(). Mirrors probe_store.py: own connection, WAL, lock, no
isolation_level (autocommit), so background-walk writes don't contend with
HTTP-request writes.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

# The actionable buckets — the ones a user can do something about. `agrees`
# (tag matches what was heard), `confused` (Whisper couldn't agree with itself),
# and `undetermined` (no detection) are informational, not surfaced as findings.
ACTIONABLE_STATUSES = ("mislabel", "bilingual", "multitrack")


@dataclass
class AuditFinding:
    canonical_path: str
    tag_lang: str | None
    detected_lang: str | None
    status: str
    languages_heard: list[str]
    n_agreeing: int | None
    n_total: int | None
    mtime: float | None
    checked_at: float
    track_languages: list[str] | None = None  # ffprobe per-track langs (multi-track)

    def to_dict(self) -> dict:
        return {
            "canonical_path": self.canonical_path,
            "tag_lang": self.tag_lang,
            "detected_lang": self.detected_lang,
            "status": self.status,
            "languages_heard": list(self.languages_heard or []),
            "n_agreeing": self.n_agreeing,
            "n_total": self.n_total,
            "mtime": self.mtime,
            "checked_at": self.checked_at,
            "track_languages": list(self.track_languages or []),
        }


class AudioAuditStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
            isolation_level=None,
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── writes ──────────────────────────────────────────────────────────────
    def upsert(
        self,
        *,
        canonical_path: str,
        tag_lang: str | None,
        detected_lang: str | None,
        status: str,
        languages_heard: list[str] | None,
        n_agreeing: int | None,
        n_total: int | None,
        mtime: float | None,
        track_languages: list[str] | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audio_lang_audit "
                "(canonical_path, tag_lang, detected_lang, status, languages_heard, "
                " n_agreeing, n_total, mtime, checked_at, track_languages) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(canonical_path) DO UPDATE SET "
                "  tag_lang=excluded.tag_lang, detected_lang=excluded.detected_lang, "
                "  status=excluded.status, languages_heard=excluded.languages_heard, "
                "  n_agreeing=excluded.n_agreeing, n_total=excluded.n_total, "
                "  mtime=excluded.mtime, checked_at=excluded.checked_at, "
                "  track_languages=excluded.track_languages",
                (
                    canonical_path,
                    tag_lang,
                    detected_lang,
                    status,
                    json.dumps(list(languages_heard or [])),
                    n_agreeing,
                    n_total,
                    mtime,
                    time.time(),
                    json.dumps(list(track_languages or [])),
                ),
            )

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM audio_lang_audit")

    # ── reads ───────────────────────────────────────────────────────────────
    @staticmethod
    def _json_list(value) -> list:
        try:
            v = json.loads(value or "[]")
        except (ValueError, TypeError):
            return []
        return v if isinstance(v, list) else []

    def _row(self, r: sqlite3.Row) -> AuditFinding:
        # track_languages is a column added in migration 011 — tolerate rows/DBs
        # that predate it (sqlite3.Row raises IndexError on a missing key).
        try:
            tracks = self._json_list(r["track_languages"])
        except (IndexError, KeyError):
            tracks = []
        return AuditFinding(
            canonical_path=r["canonical_path"],
            tag_lang=r["tag_lang"],
            detected_lang=r["detected_lang"],
            status=r["status"],
            languages_heard=self._json_list(r["languages_heard"]),
            n_agreeing=r["n_agreeing"],
            n_total=r["n_total"],
            mtime=r["mtime"],
            checked_at=r["checked_at"],
            track_languages=tracks,
        )

    def get(self, canonical_path: str) -> AuditFinding | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM audio_lang_audit WHERE canonical_path = ?",
                (canonical_path,),
            ).fetchone()
        return self._row(row) if row else None

    def list_findings(self, statuses=ACTIONABLE_STATUSES) -> list[AuditFinding]:
        """The actionable findings (mislabel/bilingual/multitrack by default),
        newest-checked first — what the review UI renders."""
        statuses = tuple(statuses)
        if not statuses:
            return []
        # `placeholders` is only "?,?,…" bind markers (one per status); the
        # status VALUES are passed as bound params below, never interpolated —
        # a safe parametrized IN-clause. Bandit's B608 flags any f-string in a
        # query, so suppress this specific false positive.
        placeholders = ",".join("?" for _ in statuses)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM audio_lang_audit WHERE status IN ({placeholders}) "  # nosec B608
                "ORDER BY checked_at DESC",
                statuses,
            ).fetchall()
        return [self._row(r) for r in rows]

    def all(self) -> list[AuditFinding]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM audio_lang_audit ORDER BY checked_at DESC").fetchall()
        return [self._row(r) for r in rows]

    def count_by_status(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM audio_lang_audit GROUP BY status"
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def scan_summary(self) -> dict:
        """Cheap freshness summary derived straight from the rows — no extra
        persistence. `total_checked` = files ever audited; `last_checked_at` =
        most recent verdict. Powers the panel's 'Last scanned X ago · N checked'
        line, which survives restarts because it reads the durable table (the
        in-memory AuditState progress does not)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n, MAX(checked_at) AS last FROM audio_lang_audit"
            ).fetchone()
        return {"total_checked": (row["n"] or 0), "last_checked_at": row["last"]}
