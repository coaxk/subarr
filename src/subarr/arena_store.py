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

from .data_persistence import apply_journal_mode

log = logging.getLogger(__name__)

# [#146] How many distinct *real* spoken languages a recipe must have data in
# before it earns a global rank. Below this it's an artifact of one easy/popular
# language, not a cross-language verdict. The "und" (undetermined) bucket never
# counts toward this — it isn't a language.
LEADERBOARD_MIN_LANGUAGES = 3


def _json_default(o):
    """Coerce numpy-style scalars/arrays (the QE judge emits float32) so a
    stray non-builtin number can never crash persistence and strand a run in
    'running'. Duck-typed — no hard numpy import in this base module."""
    if hasattr(o, "item"):  # numpy scalar
        return o.item()
    if hasattr(o, "tolist"):  # numpy array
        return o.tolist()
    raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")


@dataclass
class ArenaRun:
    id: str
    media_path: str
    variants: list[dict[str, Any]]  # [{label, kwargs}]
    source_language: str | None = None
    status: str = "pending"  # pending | queued | waiting_for_capacity | running | done | error
    source_text: str | None = None
    outcomes: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None  # serialized TournamentResult
    error: str | None = None
    created_at: float = 0.0
    # [#314] Per-sweep readability rubric: the CPS bar every recipe in this run
    # was judged against. Persisted so historical results stay interpretable.
    # Defaults to the Netflix/BBC norms (subtitle_readability MAX_CPS/CRITICAL_CPS).
    cps_max: float = 20.0
    cps_critical: float = 25.0
    # Audio-stream ordinal this sweep transcribes (multi-track files fan out one
    # run per track). In-memory only — it flows create→execute in one process;
    # not a persisted column (a restart errors the run anyway).
    track_index: int = 0
    # True when this run is one leg of a multi-track fan-out (its language came
    # from the track's tag, not a user pick). Lets the resolver label the herd
    # source "track" rather than "user". In-memory only, like track_index.
    is_track_fanout: bool = False

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
            "source_language": self.source_language,  # [#23] for the table + per-lang grouping
            # how the language was decided: "whisper" (robust majority), "user"
            # (set at submit), or "tagged" (fell back to the file's known audio
            # language when Whisper was inconclusive). Lets the UI mark a
            # tagged-not-heard label as weaker than a Whisper-confirmed one.
            "source_language_source": res.get("source_language_source"),
            # #(bilingual) per-chunk language signal: which languages Whisper
            # heard, whether they disagree with the tag (mislabel) or coexist
            # (mixed/bilingual). Drives the sweep-row "verify" badges.
            "audio_languages_heard": res.get("audio_languages_heard") or [],
            "audio_lang_mixed": bool(res.get("audio_lang_mixed")),
            "audio_lang_mislabel": bool(res.get("audio_lang_mislabel")),
            # multi-track file (original + dub): ≥2 distinct audio-track langs;
            # the sweep transcribed one. Distinct from single-track bilingual.
            "audio_multitrack": bool(res.get("audio_multitrack")),
            "audio_track_languages": res.get("audio_track_languages") or [],
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
        cps_max=r["cps_max"],
        cps_critical=r["cps_critical"],
    )


class ArenaStore:
    """Thread-safe SQLite store for arena sweeps (single conn + lock, WAL)."""

    def __init__(self, db_path: Path):
        self._path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        apply_journal_mode(self._conn, db_path)
        self._lock = threading.Lock()

    def save(self, run: ArenaRun) -> None:
        """Upsert the full run (write-through on every state transition)."""
        winner = (run.result or {}).get("winner_label")
        with self._lock:
            self._conn.execute(
                """INSERT INTO arena_runs
                     (id, media_path, source_language, status, source_text,
                      variants, outcomes, result, winner, error, created_at, updated_at,
                      cps_max, cps_critical)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     status=excluded.status, source_text=excluded.source_text,
                     source_language=excluded.source_language,
                     outcomes=excluded.outcomes, result=excluded.result,
                     winner=excluded.winner, error=excluded.error,
                     updated_at=excluded.updated_at""",
                (
                    run.id,
                    run.media_path,
                    run.source_language,
                    run.status,
                    run.source_text,
                    json.dumps(run.variants, default=_json_default),
                    json.dumps(run.outcomes, default=_json_default),
                    json.dumps(run.result, default=_json_default) if run.result is not None else None,
                    winner,
                    run.error,
                    run.created_at,
                    time.time(),
                    run.cps_max,
                    run.cps_critical,
                ),
            )

    def get(self, run_id: str) -> ArenaRun | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM arena_runs WHERE id = ?", (run_id,)).fetchone()
        return _row_to_run(row) if row else None

    def list(self, limit: int = 200) -> list[ArenaRun]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM arena_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
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
        runs = [r for r in self.list(limit=1000) if r.status == "done" and r.source_language and r.result]
        return aggregate_runs_by_language(runs)

    def aggregate_global_leaderboard(
        self,
        *,
        min_languages: int = LEADERBOARD_MIN_LANGUAGES,
    ) -> list[dict[str, Any]]:
        """[#146] Overall recipe ranking (mean of per-language means). Same
        completed-sweep filter as aggregate_by_language; delegates to the pure
        module function."""
        runs = [r for r in self.list(limit=1000) if r.status == "done" and r.source_language and r.result]
        return aggregate_global_leaderboard(runs, min_languages=min_languages)

    def prune_older_than(self, cutoff_epoch: float) -> int:
        """[#136] Age-based retention: drop arena_runs whose created_at predates
        cutoff_epoch. arena_runs is append-only (a row per sweep, never deleted
        except by the user) so it grows unbounded on long-running installs. This
        mirrors scan_store.delete_where_status_in's age-cutoff DELETE; called on
        boot from the app lifespan (idx_arena_runs_created keeps it cheap)."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM arena_runs WHERE created_at < ?", (cutoff_epoch,))
            return cur.rowcount or 0

    def reconcile_interrupted(self) -> int:
        """A run that was pending/queued/running when the process died can never
        finish — its asyncio task is gone. Mark such rows as errored on boot
        so the UI shows the truth instead of a forever-spinning sweep."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE arena_runs SET status='error', error='interrupted by restart', updated_at=? "
                "WHERE status IN ('pending', 'queued', 'running', 'waiting_for_capacity')",
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
        # Bucket a sweep with no resolved language under "undetermined" (und)
        # rather than dropping it — a sweep that ran but whose language Whisper
        # couldn't agree on (and that had no tagged fallback) should still be
        # visible in the herd, not vanish.
        lang = normalize_lang(getattr(r, "source_language", None)) or "und"
        result = getattr(r, "result", None) or {}
        if not result:
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
        if f["_winvotes"]:  # the file's winner = its per-sweep majority
            file_winner = max(f["_winvotes"].items(), key=lambda kv: kv[1])[0]
        elif file_means:  # all ties → best mean is the de-facto winner
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
        recipes = [
            {
                "label": rec["label"],
                "files": rec["files"],
                "wins": rec["wins"],
                "mean_composite": round(rec["_sum"] / rec["files"], 2) if rec["files"] else 0.0,
            }
            for rec in b["_rec"].values()
        ]
        # Rank by WINS (consistency across this language's files), tie-broken by
        # mean composite. The top row is the per-language *default recommendation*
        # — for a config you apply to all content, the one that ranks #1 most
        # often is the safer pick than a specialist that scores high on one file
        # but loses elsewhere. Score is shown alongside for the specialist nuance
        # (the UI explains wins-vs-score). (#26/#141 ranking note)
        recipes.sort(key=lambda x: (x["wins"], x["mean_composite"]), reverse=True)
        out.append(
            {"language": b["language"], "files": b["files"], "sweeps": b["sweeps"], "recipes": recipes}
        )
    out.sort(key=lambda x: (x["files"], x["sweeps"]), reverse=True)
    return out


def aggregate_global_leaderboard(
    runs, *, min_languages: int = LEADERBOARD_MIN_LANGUAGES
) -> list[dict[str, Any]]:
    """[#146] Roll the per-language herd up one level into a single overall
    recipe ranking — the 'recipe leaderboard'.

    Ranks by the MEAN OF PER-LANGUAGE MEANS (each language weighted equally),
    NOT a flat mean across all files — otherwise heavily-swept / easy languages
    (English) would skew the global ranking. Reuses aggregate_runs_by_language
    and averages each recipe's per-language mean_composite values.

    The "und" bucket is excluded — it's not a real language and would let a recipe
    game the cross-language threshold. A recipe with data in fewer than
    ``min_languages`` real languages is returned but marked ``eligible=False``
    (and ``rank=None``) so the UI can show it as 'still accumulating' without
    pretending it's a ranked verdict.

    Each row: {label, rank (1..k for eligible, else None), global_mean,
    languages_covered, total_files, total_wins, eligible, confidence,
    per_language:[{language, files, wins, mean_composite}, ...]}.
    """
    by_lang = aggregate_runs_by_language(runs)
    if not by_lang:
        return []

    recipes: dict[str, dict] = {}
    for bucket in by_lang:
        lang = bucket["language"]
        if lang == "und":  # not a real language — never counts
            continue
        for r in bucket["recipes"]:
            label = r["label"]
            entry = recipes.setdefault(
                label,
                {
                    "label": label,
                    "_means": [],
                    "_detail": [],
                    "total_wins": 0,
                    "total_files": 0,
                },
            )
            entry["_means"].append(float(r["mean_composite"]))
            entry["total_wins"] += r["wins"]
            entry["total_files"] += r["files"]
            entry["_detail"].append(
                {
                    "language": lang,
                    "files": r["files"],
                    "wins": r["wins"],
                    "mean_composite": r["mean_composite"],
                }
            )

    rows = []
    for entry in recipes.values():
        means = entry["_means"]
        lang_count = len(means)
        global_mean = round(sum(means) / lang_count, 2) if means else 0.0
        eligible = lang_count >= min_languages
        if lang_count >= 3 and entry["total_files"] >= 10:
            confidence = "high"
        elif lang_count >= 2 or entry["total_files"] >= 5:
            confidence = "moderate"
        else:
            confidence = "low"
        detail = sorted(entry["_detail"], key=lambda x: x["files"], reverse=True)
        rows.append(
            {
                "label": entry["label"],
                "global_mean": global_mean,
                "languages_covered": lang_count,
                "total_files": entry["total_files"],
                "total_wins": entry["total_wins"],
                "eligible": eligible,
                "confidence": confidence,
                "per_language": detail,
            }
        )

    # Eligible recipes first, then by global_mean desc, tie-broken by total_wins.
    rows.sort(key=lambda x: (x["eligible"], x["global_mean"], x["total_wins"]), reverse=True)
    rank = 0
    for row in rows:
        if row["eligible"]:
            rank += 1
            row["rank"] = rank
        else:
            row["rank"] = None
    return rows
