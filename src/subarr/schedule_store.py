"""SQLite-backed schedule + auto-queue rules persistence.

Two singleton-ish tables in subarr.db:

`schedule_config`: one row per scheduled job. For v1.2 batch 1 we ship a
single canonical job named 'coverage_walk' that re-pulls the Coverage
report and applies the auto-queue rules. Future jobs (ledger compaction,
LLM enrichment cycles) plug in by adding rows.

`auto_queue_rules`: single-row config (id=1 sentinel) holding the user's
auto-queue policy: mode + score threshold + lang allow/deny + tag
allow/deny + max items per run.

Both tables are tiny (≤N rows) so a single Lock around the connection
is fine.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Schedule kinds:
KIND_INTERVAL = "interval"  # fires every interval_minutes
KIND_DAILY = "daily"        # fires once per day at HH:MM (24h)
KIND_WEEKLY = "weekly"      # fires once per week on day_of_week at HH:MM


# Auto-queue rule modes (matches spec s.1.2):
MODE_DASHBOARD = "dashboard"           # Coverage list only, no queue action
MODE_MANUAL_CONFIRM = "manual_confirm"  # show "queue all matching" button
MODE_AUTO_RULES = "auto_rules"          # automatically queue matching rows


@dataclass
class ScheduleConfig:
    name: str
    enabled: bool
    kind: str
    interval_minutes: int        # for KIND_INTERVAL
    daily_hhmm: str              # for KIND_DAILY / KIND_WEEKLY ("03:00")
    day_of_week: int             # for KIND_WEEKLY (0=Mon, 6=Sun)
    last_run_at: float | None
    next_run_at: float | None
    last_result: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "kind": self.kind,
            "interval_minutes": self.interval_minutes,
            "daily_hhmm": self.daily_hhmm,
            "day_of_week": self.day_of_week,
            "last_run_at": self.last_run_at,
            "next_run_at": self.next_run_at,
            "last_result": self.last_result,
        }


@dataclass
class AutoQueueRules:
    mode: str = MODE_DASHBOARD
    min_score: int = 200
    allow_languages: list[str] = field(default_factory=list)  # empty = all
    deny_languages: list[str] = field(default_factory=lambda: ["English"])
    allow_tags: list[str] = field(default_factory=list)
    deny_tags: list[str] = field(default_factory=list)
    require_monitored: bool = True
    skip_stale_disk: bool = True   # don't auto-queue rows where .srt already on disk
    max_per_run: int = 50

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "min_score": self.min_score,
            "allow_languages": self.allow_languages,
            "deny_languages": self.deny_languages,
            "allow_tags": self.allow_tags,
            "deny_tags": self.deny_tags,
            "require_monitored": self.require_monitored,
            "skip_stale_disk": self.skip_stale_disk,
            "max_per_run": self.max_per_run,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AutoQueueRules":
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in valid})


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schedule_config (
    name             TEXT PRIMARY KEY,
    enabled          INTEGER NOT NULL DEFAULT 0,
    kind             TEXT NOT NULL,
    interval_minutes INTEGER NOT NULL DEFAULT 360,
    daily_hhmm       TEXT NOT NULL DEFAULT '03:00',
    day_of_week      INTEGER NOT NULL DEFAULT 0,
    last_run_at      REAL,
    next_run_at      REAL,
    last_result      TEXT
);
CREATE TABLE IF NOT EXISTS auto_queue_rules (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    payload_json     TEXT NOT NULL,
    updated_at       REAL NOT NULL
);
"""


_DEFAULT_SCHEDULE = ScheduleConfig(
    name="coverage_walk",
    enabled=False,                # off by default — user opts in
    kind=KIND_DAILY,
    interval_minutes=360,
    daily_hhmm="03:00",
    day_of_week=0,
    last_run_at=None,
    next_run_at=None,
    last_result=None,
)


class ScheduleStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()

    def init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
            row = self._conn.execute(
                "SELECT name FROM schedule_config WHERE name = ?",
                (_DEFAULT_SCHEDULE.name,),
            ).fetchone()
            if not row:
                self._conn.execute(
                    "INSERT INTO schedule_config "
                    "(name, enabled, kind, interval_minutes, daily_hhmm, day_of_week) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        _DEFAULT_SCHEDULE.name,
                        1 if _DEFAULT_SCHEDULE.enabled else 0,
                        _DEFAULT_SCHEDULE.kind,
                        _DEFAULT_SCHEDULE.interval_minutes,
                        _DEFAULT_SCHEDULE.daily_hhmm,
                        _DEFAULT_SCHEDULE.day_of_week,
                    ),
                )
            rules_row = self._conn.execute(
                "SELECT id FROM auto_queue_rules WHERE id = 1"
            ).fetchone()
            if not rules_row:
                self._conn.execute(
                    "INSERT INTO auto_queue_rules (id, payload_json, updated_at) "
                    "VALUES (1, ?, ?)",
                    (json.dumps(AutoQueueRules().to_dict()), time.time()),
                )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── schedule_config ─────────────────────────────────────────────────

    def get_schedule(self, name: str) -> ScheduleConfig | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT name, enabled, kind, interval_minutes, daily_hhmm, day_of_week, "
                "       last_run_at, next_run_at, last_result "
                "FROM schedule_config WHERE name = ?",
                (name,),
            ).fetchone()
        if row is None:
            return None
        return ScheduleConfig(
            name=row[0], enabled=bool(row[1]), kind=row[2],
            interval_minutes=row[3], daily_hhmm=row[4], day_of_week=row[5],
            last_run_at=row[6], next_run_at=row[7], last_result=row[8],
        )

    def list_schedules(self) -> list[ScheduleConfig]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, enabled, kind, interval_minutes, daily_hhmm, day_of_week, "
                "       last_run_at, next_run_at, last_result "
                "FROM schedule_config ORDER BY name"
            ).fetchall()
        return [
            ScheduleConfig(
                name=r[0], enabled=bool(r[1]), kind=r[2],
                interval_minutes=r[3], daily_hhmm=r[4], day_of_week=r[5],
                last_run_at=r[6], next_run_at=r[7], last_result=r[8],
            ) for r in rows
        ]

    def update_schedule(self, name: str, *, enabled: bool | None = None,
                         kind: str | None = None,
                         interval_minutes: int | None = None,
                         daily_hhmm: str | None = None,
                         day_of_week: int | None = None) -> ScheduleConfig:
        with self._lock:
            current = self._conn.execute(
                "SELECT enabled, kind, interval_minutes, daily_hhmm, day_of_week "
                "FROM schedule_config WHERE name = ?",
                (name,),
            ).fetchone()
            if not current:
                raise KeyError(f"schedule {name!r} not found")
            new = (
                int(enabled) if enabled is not None else current[0],
                kind or current[1],
                interval_minutes if interval_minutes is not None else current[2],
                daily_hhmm or current[3],
                day_of_week if day_of_week is not None else current[4],
            )
            self._conn.execute(
                "UPDATE schedule_config SET enabled=?, kind=?, interval_minutes=?, "
                "       daily_hhmm=?, day_of_week=? WHERE name=?",
                (*new, name),
            )
        return self.get_schedule(name)  # type: ignore[return-value]

    def record_run(self, name: str, *, last_run_at: float, next_run_at: float | None,
                   last_result: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE schedule_config SET last_run_at=?, next_run_at=?, last_result=? "
                "WHERE name=?",
                (last_run_at, next_run_at, last_result, name),
            )

    # ── auto_queue_rules ────────────────────────────────────────────────

    def get_rules(self) -> AutoQueueRules:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload_json FROM auto_queue_rules WHERE id = 1"
            ).fetchone()
        if not row:
            return AutoQueueRules()
        try:
            return AutoQueueRules.from_dict(json.loads(row[0]))
        except (ValueError, TypeError):
            return AutoQueueRules()

    def set_rules(self, rules: AutoQueueRules) -> AutoQueueRules:
        with self._lock:
            self._conn.execute(
                "UPDATE auto_queue_rules SET payload_json=?, updated_at=? WHERE id=1",
                (json.dumps(rules.to_dict()), time.time()),
            )
        return rules
