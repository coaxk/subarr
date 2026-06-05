"""#131 — SQLite persistence for tuning-lab sweeps.

Sweeps (and their ranked results) persist so history survives a restart, and
so they can later feed the federated tournament (#124): the per-recipe scores
keyed by language/content are exactly the signal crowd-curated per-language
presets are built from.

`ArenaRun` is the in-flight + persisted representation (defined here so the
store and service share it without a circular import; re-exported from
arena_service for stable imports). The store mirrors ErrorStore/ScanStore:
single connection + lock + WAL, schema owned by migrations (no init here).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def _json_default(o):
    """Coerce numpy-style scalars/arrays (the QE judge emits float32) so a
    stray non-builtin number can never crash persistence and strand a run in
    'running'. Duck-typed — no hard numpy import in this base module."""
    if hasattr(o, "item"):       # numpy scalar
        return o.item()
    if hasattr(o, "tolist"):     # numpy array
        return o.tolist()
    raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")


@dataclass
class ArenaRun:
    id: str
    media_path: str
    variants: list[dict[str, Any]]          # [{label, kwargs}]
    source_language: str | None = None
    status: str = "pending"                  # pending | queued | running | done | error
    source_text: str | None = None
    outcomes: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None     # serialized TournamentResult
    error: str | None = None
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> dict[str, Any]:
        """Lightweight shape for the sweeps list — no heavy scorecards. Full
        detail (aggregate + per-clip) is fetched per-run via /{id}."""
        prog = self.outcomes if isinstance(self.outcomes, dict) else {}
        res = self.result or {}
        return {
            "id": self.id,
            "media_path": self.media_path,
            "source_language": self.source_language,   # [#23] for the table + per-lang grouping
            "status": self.status,
            "recipe_count": len(self.variants),
            "clips_total": len(prog.get("clips", [])),
            "steps_done": prog.get("done", 0),
            "steps_total": prog.get("total", 0),
            "winner": res.get("winner"),
            "confidence": res.get("confidence"),
            "tie": res.get("tie", False),
            "error": self.error,
            "created_at": self.created_at,
        }


def _row_to_run(r: sqlite3.Row) -> ArenaRun:
    return ArenaRun(
        id=r["id"],
        media_path=r["media_path"],
        source_language=r["source_language"],
        status=r["status"],
        source_text=r["source_text"],
        variants=json.loads(r["variants"]),
        outcomes=json.loads(r["outcomes"]),
        result=json.loads(r["result"]) if r["result"] else None,
        error=r["error"],
        created_at=r["created_at"],
    )


class ArenaStore:
    """Thread-safe SQLite store for arena sweeps (single conn + lock, WAL)."""

    def __init__(self, db_path: Path):
        self._path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()

    def save(self, run: ArenaRun) -> None:
        """Upsert the full run (write-through on every state transition)."""
        winner = (run.result or {}).get("winner_label")
        with self._lock:
            self._conn.execute(
                """INSERT INTO arena_runs
                     (id, media_path, source_language, status, source_text,
                      variants, outcomes, result, winner, error, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     status=excluded.status, source_text=excluded.source_text,
                     source_language=excluded.source_language,
                     outcomes=excluded.outcomes, result=excluded.result,
                     winner=excluded.winner, error=excluded.error,
                     updated_at=excluded.updated_at""",
                (run.id, run.media_path, run.source_language, run.status, run.source_text,
                 json.dumps(run.variants, default=_json_default),
                 json.dumps(run.outcomes, default=_json_default),
                 json.dumps(run.result, default=_json_default) if run.result is not None else None,
                 winner, run.error, run.created_at, time.time()),
            )

    def get(self, run_id: str) -> ArenaRun | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM arena_runs WHERE id = ?", (run_id,)).fetchone()
        return _row_to_run(row) if row else None

    def list(self, limit: int = 200) -> list[ArenaRun]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM arena_runs ORDER BY created_at DESC LIMIT ?", (limit,),
            ).fetchall()
        return [_row_to_run(r) for r in rows]

    def delete(self, run_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM arena_runs WHERE id = ?", (run_id,))
            return (cur.rowcount or 0) > 0

    def aggregate_by_language(self) -> list[dict[str, Any]]:
        """[#26] Group completed, language-detected sweeps by source language →
        per-recipe stats. The 'herd' foundation for per-language presets +
        federated #124."""
        runs = [r for r in self.list(limit=1000)
                if r.status == "done" and r.source_language and r.result]
        return aggregate_runs_by_language(runs)

    def reconcile_interrupted(self) -> int:
        """A run that was pending/queued/running when the process died can never
        finish — its asyncio task is gone. Mark such rows as errored on boot
        so the UI shows the truth instead of a forever-spinning sweep."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE arena_runs SET status='error', error='interrupted by restart', updated_at=? "
                "WHERE status IN ('pending', 'queued', 'running')",
                (time.time(),),
            )
            return cur.rowcount or 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def aggregate_runs_by_language(runs) -> list[dict[str, Any]]:
    """[#26] Pure: group sweeps by detected language → per-recipe stats. CONSOLIDATES
    per file first (a file's repeat sweeps are averaged into ONE estimate, then each
    FILE casts one vote) so bulk/repeat sweeping CONFIRMS rather than SKEWS — one
    heavily-swept file can't dominate its language bucket.

    Per language returns: files (distinct), sweeps (total), and recipes ranked by
    files-won then mean composite, each = {label, files, wins (files won),
    mean_composite (mean of per-file means)}. Tie → no win for anyone."""
    from .langs import normalize_lang

    # 1. Collapse to per-(language, file): average each recipe across the file's
    #    repeat sweeps; tally per-sweep winner votes for the file.
    files: dict[tuple, dict] = {}
    for r in runs:
        lang = normalize_lang(getattr(r, "source_language", None))
        result = getattr(r, "result", None) or {}
        if not lang or not result:
            continue
        key = (lang, getattr(r, "media_path", None))
        f = files.setdefault(key, {"lang": lang, "sweeps": 0, "_rec": {}, "_winvotes": {}})
        f["sweeps"] += 1
        winner = result.get("winner") if not result.get("tie") else None
        if winner:
            f["_winvotes"][winner] = f["_winvotes"].get(winner, 0) + 1
        for row in result.get("aggregate") or []:
            label = row.get("label")
            if not label:
                continue
            rec = f["_rec"].setdefault(label, {"_sum": 0.0, "n": 0})
            rec["_sum"] += row.get("mean_composite") or 0.0
            rec["n"] += 1

    # 2. Roll files up into language buckets — each file contributes one vote.
    langs: dict[str, dict] = {}
    for (lang, _path), f in files.items():
        file_means = {label: rec["_sum"] / rec["n"] for label, rec in f["_rec"].items() if rec["n"]}
        if f["_winvotes"]:                       # the file's winner = its per-sweep majority
            file_winner = max(f["_winvotes"].items(), key=lambda kv: kv[1])[0]
        elif file_means:                         # all ties → best mean is the de-facto winner
            file_winner = max(file_means.items(), key=lambda kv: kv[1])[0]
        else:
            file_winner = None
        b = langs.setdefault(lang, {"language": lang, "files": 0, "sweeps": 0, "_rec": {}})
        b["files"] += 1
        b["sweeps"] += f["sweeps"]
        for label, mean in file_means.items():
            rec = b["_rec"].setdefault(label, {"label": label, "files": 0, "wins": 0, "_sum": 0.0})
            rec["files"] += 1
            rec["_sum"] += mean
            if label == file_winner:
                rec["wins"] += 1

    out = []
    for b in langs.values():
        recipes = [{"label": rec["label"], "files": rec["files"], "wins": rec["wins"],
                    "mean_composite": round(rec["_sum"] / rec["files"], 2) if rec["files"] else 0.0}
                   for rec in b["_rec"].values()]
        # Rank by WINS (consistency across this language's files), tie-broken by
        # mean composite. The top row is the per-language *default recommendation*
        # — for a config you apply to all content, the one that ranks #1 most
        # often is the safer pick than a specialist that scores high on one file
        # but loses elsewhere. Score is shown alongside for the specialist nuance
        # (the UI explains wins-vs-score). (#26/#141 ranking note)
        recipes.sort(key=lambda x: (x["wins"], x["mean_composite"]), reverse=True)
        out.append({"language": b["language"], "files": b["files"], "sweeps": b["sweeps"], "recipes": recipes})
    out.sort(key=lambda x: (x["files"], x["sweeps"]), reverse=True)
    return out
