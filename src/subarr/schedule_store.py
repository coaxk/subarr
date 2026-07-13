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

from .data_persistence import apply_journal_mode


# Schedule kinds:
KIND_INTERVAL = "interval"  # fires every interval_minutes
KIND_DAILY = "daily"  # fires once per day at HH:MM (24h)
KIND_WEEKLY = "weekly"  # fires once per week on day_of_week at HH:MM


# Auto-queue rule modes (matches spec s.1.2):
MODE_DASHBOARD = "dashboard"  # Coverage list only, no queue action
MODE_MANUAL_CONFIRM = "manual_confirm"  # show "queue all matching" button
MODE_AUTO_RULES = "auto_rules"  # automatically queue matching rows


@dataclass
class ScheduleConfig:
    name: str
    enabled: bool
    kind: str
    interval_minutes: int  # for KIND_INTERVAL
    daily_hhmm: str  # for KIND_DAILY / KIND_WEEKLY ("03:00")
    day_of_week: int  # for KIND_WEEKLY (0=Mon, 6=Sun)
    last_run_at: float | None
    next_run_at: float | None
    last_result: str | None
    # 2026-05-27: comma-separated canonical paths to probe-walk BEFORE
    # this schedule's coverage_walk runs. Empty = no probe step. Incremental
    # (probe cache skips unchanged files via mtime+size check) so re-walking
    # the same root is cheap after the first run.
    probe_roots: str = ""

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
            "probe_roots": [p.strip() for p in (self.probe_roots or "").split(",") if p.strip()],
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
    skip_stale_disk: bool = True  # don't auto-queue rows where .srt already on disk
    skip_embedded_en: bool = True  # don't auto-queue rows where probe confirmed EN/EN(SDH)
    max_per_run: int = 50
    # #117 settle-window: hold a freshly-imported gap out of auto-queue for
    # this many minutes after Sonarr/Radarr imported it, so Bazarr/providers
    # get first crack at landing a real sub before subarr burns GPU on a
    # transcription. 0 = disabled (default — opt-in, no behavior change).
    # Manual transcribe always bypasses this (it doesn't run through evaluate()).
    settle_minutes: int = 0
    # #66/#116 queue authority: the feeder keeps subgen filled to this many
    # concurrent jobs (queued+processing, total — foreign work counts) and no
    # more, so the rest of the backlog stays in subarr's reorderable/pausable
    # pending queue. `queue_paused` halts the feed (in-flight subgen jobs keep
    # running). target_depth small (2) keeps reorder meaningful; higher = more
    # rushes into subgen + less reorderable.
    queue_target_depth: int = 2
    queue_paused: bool = False

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
            "skip_embedded_en": self.skip_embedded_en,
            "max_per_run": self.max_per_run,
            "settle_minutes": self.settle_minutes,
            "queue_target_depth": self.queue_target_depth,
            "queue_paused": self.queue_paused,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AutoQueueRules":
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in valid})


# Schema (schedule_config, auto_queue_rules) is owned by
# migrations/001_baseline.sql, and the default disabled 'coverage_walk'
# schedule row is seeded by migrations/008_init_schema_parity.sql.
# run_migrations() runs at boot before this store — no per-store
# init_schema(). auto_queue_rules needs no seed: get_rules() returns a
# default AutoQueueRules() when the row is absent.


class ScheduleStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        apply_journal_mode(self._conn, db_path)
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── schedule_config ─────────────────────────────────────────────────

    def get_schedule(self, name: str) -> ScheduleConfig | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT name, enabled, kind, interval_minutes, daily_hhmm, day_of_week, "
                "       last_run_at, next_run_at, last_result, probe_roots "
                "FROM schedule_config WHERE name = ?",
                (name,),
            ).fetchone()
        if row is None:
            return None
        return ScheduleConfig(
            name=row[0],
            enabled=bool(row[1]),
            kind=row[2],
            interval_minutes=row[3],
            daily_hhmm=row[4],
            day_of_week=row[5],
            last_run_at=row[6],
            next_run_at=row[7],
            last_result=row[8],
            probe_roots=row[9] or "",
        )

    def list_schedules(self) -> list[ScheduleConfig]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, enabled, kind, interval_minutes, daily_hhmm, day_of_week, "
                "       last_run_at, next_run_at, last_result, probe_roots "
                "FROM schedule_config ORDER BY name"
            ).fetchall()
        return [
            ScheduleConfig(
                name=r[0],
                enabled=bool(r[1]),
                kind=r[2],
                interval_minutes=r[3],
                daily_hhmm=r[4],
                day_of_week=r[5],
                last_run_at=r[6],
                next_run_at=r[7],
                last_result=r[8],
                probe_roots=r[9] or "",
            )
            for r in rows
        ]

    def update_schedule(
        self,
        name: str,
        *,
        enabled: bool | None = None,
        kind: str | None = None,
        interval_minutes: int | None = None,
        daily_hhmm: str | None = None,
        day_of_week: int | None = None,
        probe_roots: str | None = None,
    ) -> ScheduleConfig:
        with self._lock:
            current = self._conn.execute(
                "SELECT enabled, kind, interval_minutes, daily_hhmm, day_of_week, probe_roots "
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
                probe_roots if probe_roots is not None else current[5],
            )
            self._conn.execute(
                "UPDATE schedule_config SET enabled=?, kind=?, interval_minutes=?, "
                "       daily_hhmm=?, day_of_week=?, probe_roots=? WHERE name=?",
                (*new, name),
            )
        return self.get_schedule(name)  # type: ignore[return-value]

    def record_run(
        self, name: str, *, last_run_at: float, next_run_at: float | None, last_result: str
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE schedule_config SET last_run_at=?, next_run_at=?, last_result=? WHERE name=?",
                (last_run_at, next_run_at, last_result, name),
            )

    # ── auto_queue_rules ────────────────────────────────────────────────

    def get_rules(self) -> AutoQueueRules:
        with self._lock:
            row = self._conn.execute("SELECT payload_json FROM auto_queue_rules WHERE id = 1").fetchone()
        if not row:
            return AutoQueueRules()
        try:
            return AutoQueueRules.from_dict(json.loads(row[0]))
        except (ValueError, TypeError):
            return AutoQueueRules()

    def set_rules(self, rules: AutoQueueRules) -> AutoQueueRules:
        # Upsert: auto_queue_rules is no longer seeded at boot (init_schema
        # removed; get_rules() defaults when absent), so an UPDATE-only would
        # silently no-op the first time a user saves rules. INSERT the id=1
        # row if missing, otherwise replace its payload.
        with self._lock:
            self._conn.execute(
                "INSERT INTO auto_queue_rules (id, payload_json, updated_at) "
                "VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "  payload_json=excluded.payload_json, "
                "  updated_at=excluded.updated_at",
                (json.dumps(rules.to_dict()), time.time()),
            )
        return rules
