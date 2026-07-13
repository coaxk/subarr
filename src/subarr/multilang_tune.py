"""#407 off-app tuning aid: sweep the multilingual chunk-confidence threshold T
across the accrued per-chunk-probability corpus and report how the multilingual
classification and the probability distribution respond. Read-only, CPU-only --
never runs detection, only reads stored `chunks_conf`. Reuses
`classify_high_conf_langs` so the corpus reader and the live classifier can never
diverge. The actual T-bake is Part B (data-gated on accrued multilingual positives)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from statistics import median

from .multilang import classify_high_conf_langs

# A corpus entry is one file's chunks_conf: [[lang, prob], ...] (JSON-decoded -> lists).
ChunksConf = list


def t_grid() -> list[float]:
    """Candidate thresholds to sweep. The live default is 0.5 (config.py)."""
    return [0.3, 0.4, 0.5, 0.6, 0.7, 0.8]


@dataclass(frozen=True)
class SweepRow:
    t: float
    files: int
    multilingual: int  # >=2 high-conf distinct langs (would classify 'multilingual')
    single: int  # exactly 1 high-conf lang
    confused: int  # 0 (no chunk >= T)


def multilang_sweep(corpus: list[ChunksConf], grid: list[float]) -> list[SweepRow]:
    """For each T, count how the corpus splits into multilingual / single /
    confused, using the SAME classifier the live path uses."""
    rows: list[SweepRow] = []
    for t in grid:
        multi = single = confused = 0
        for cc in corpus:
            n = len(classify_high_conf_langs(cc, t))
            if n >= 2:
                multi += 1
            elif n == 1:
                single += 1
            else:
                confused += 1
        rows.append(SweepRow(t=t, files=len(corpus), multilingual=multi, single=single, confused=confused))
    return rows


def prob_distribution(corpus: list[ChunksConf]) -> dict:
    """Quantiles of all per-chunk probabilities across the corpus."""
    probs: list[float] = []
    for cc in corpus:
        for pair in cc or []:
            if isinstance(pair, (list, tuple)) and len(pair) >= 2 and isinstance(pair[1], (int, float)):
                probs.append(float(pair[1]))
    if not probs:
        return {"n": 0}
    probs.sort()

    def q(frac: float) -> float:
        return probs[min(len(probs) - 1, int(frac * len(probs)))]

    return {
        "n": len(probs),
        "min": probs[0],
        "p25": q(0.25),
        "median": median(probs),
        "p75": q(0.75),
        "max": probs[-1],
    }


def corpus_from_audit_store(db_path: str, *, limit: int | None = None) -> list[ChunksConf]:
    """Read every non-NULL chunks_conf from audio_lang_audit. Read-only; opens its
    own connection (off-app tool). Malformed JSON / empty lists are skipped."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT chunks_conf FROM audio_lang_audit WHERE chunks_conf IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    out: list[ChunksConf] = []
    for (raw,) in rows:
        if limit is not None and len(out) >= limit:
            break
        try:
            cc = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(cc, list) and cc:
            out.append(cc)
    return out


def format_report(rows: list[SweepRow], dist: dict) -> str:
    files = rows[0].files if rows else 0
    lines = ["# multilingual chunk-confidence T sweep (read-only; live default T=0.5)"]
    if dist.get("n"):
        lines.append(
            f"corpus: {files} files with chunks_conf | per-chunk prob n={dist['n']} "
            f"min={dist['min']:.2f} p25={dist['p25']:.2f} median={dist['median']:.2f} "
            f"p75={dist['p75']:.2f} max={dist['max']:.2f}"
        )
    else:
        lines.append("corpus: empty (no chunks_conf captured yet -- run the audio-lang audit first)")
    for r in rows:
        lines.append(f"T={r.t:.2f} | multilingual={r.multilingual} single={r.single} confused={r.confused}")
    return "\n".join(lines)
