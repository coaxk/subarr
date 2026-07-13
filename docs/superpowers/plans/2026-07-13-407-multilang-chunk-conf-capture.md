# #407 Part A — Per-chunk-probability capture: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Persist `chunks_conf` (the raw per-chunk `(language, probability)` list) during the bulk audio-language audit, so the multilingual threshold `T` can be tuned later from real data. No GPU, no change to detection behavior (T stays 0.5).

**Architecture:** One additive nullable JSON column on `audio_lang_audit`; the store's `upsert`/`AuditFinding`/`_row` learn about it; `audio_audit._audit_one` passes the `chunks_conf` it already computes; a read-only inspector (`multilang_tune.py` + CLI) reports the T-sweep and probability distribution over the accrued corpus, reusing `classify_high_conf_langs`.

**Tech Stack:** Python 3.11, SQLite (WAL, migration-owned schema), pytest. Spec: `docs/superpowers/specs/2026-07-13-407-multilang-chunk-conf-capture-design.md`.

---

## File Structure
- **Create** `src/subarr/migrations/029_audio_audit_chunks_conf.sql` — additive nullable column.
- **Modify** `src/subarr/audio_audit_store.py` — `AuditFinding.chunks_conf` field + `to_dict`; `upsert` param + column; `_row` tolerant parse.
- **Modify** `src/subarr/audio_audit.py` — `_audit_one` passes `chunks_conf` into the existing `upsert`.
- **Create** `src/subarr/multilang_tune.py` — read-only sweep/report (mirrors `retime_tune.py`).
- **Create** `scripts/multilang_tune.py` — CLI wrapper.
- **Tests**: extend `tests/test_audio_audit_store.py`, extend the audit-walker test (`tests/test_audio_audit.py`), create `tests/test_multilang_tune.py`.

---

## Task 1: Migration + store learns `chunks_conf`

**Files:**
- Create: `src/subarr/migrations/029_audio_audit_chunks_conf.sql`
- Modify: `src/subarr/audio_audit_store.py`
- Test: `tests/test_audio_audit_store.py`

- [ ] **Step 1: Write the migration**

```sql
-- 029_audio_audit_chunks_conf.sql
--
-- #407 Part A: persist the raw per-chunk (language, probability) list from robust
-- detection so the multilingual chunk-confidence threshold T can be tuned from
-- real accrued data. Additive + nullable; existing rows predate capture and read
-- back as NULL (no backfill). Payload is tiny (~3 [lang, prob] pairs per file).
ALTER TABLE audio_lang_audit ADD COLUMN chunks_conf TEXT;  -- JSON [[lang, prob], ...]
```

- [ ] **Step 2: Write the failing test** — add to `tests/test_audio_audit_store.py` (follow the file's existing DB-setup fixture that runs migrations on a temp DB):

```python
def test_upsert_round_trips_chunks_conf(audit_store):
    audit_store.upsert(
        canonical_path="lib::/a.mkv", tag_lang="en", detected_lang="gl",
        status="multilingual", languages_heard=["gl", "es"], n_agreeing=2, n_total=3,
        mtime=1.0, track_languages=None, chunks_conf=[("gl", 0.94), ("es", 0.88), ("fr", 0.71)],
    )
    f = audit_store.get("lib::/a.mkv")
    assert f.chunks_conf == [["gl", 0.94], ["es", 0.88], ["fr", 0.71]]  # JSON round-trip -> lists
    assert f.to_dict()["chunks_conf"] == [["gl", 0.94], ["es", 0.88], ["fr", 0.71]]


def test_chunks_conf_defaults_to_none_when_absent(audit_store):
    audit_store.upsert(
        canonical_path="lib::/b.mkv", tag_lang="en", detected_lang="en", status="agrees",
        languages_heard=["en"], n_agreeing=3, n_total=3, mtime=1.0, track_languages=None,
    )
    assert audit_store.get("lib::/b.mkv").chunks_conf is None
```

If the file has no `audit_store` fixture, add one that runs migrations on a `tmp_path` DB and yields an `AudioAuditStore` (match the setup already used by the other tests in this file).

- [ ] **Step 3: Run to verify it fails**

Run: `python -m pytest tests/test_audio_audit_store.py -q -k chunks_conf`
Expected: FAIL — `upsert() got an unexpected keyword argument 'chunks_conf'`.

- [ ] **Step 4: Implement the store changes** in `src/subarr/audio_audit_store.py`:

(a) `AuditFinding` — add a field (after `track_languages`):
```python
    chunks_conf: list | None = None  # #407: raw per-chunk [ [lang, prob], ... ], NULL pre-capture
```
and in `to_dict`, add:
```python
            "chunks_conf": self.chunks_conf,
```

(b) `upsert` — add the keyword-only param (after `track_languages`):
```python
        chunks_conf: list | None = None,
```
Add `chunks_conf` to the INSERT column list and its `?`, add `chunks_conf=excluded.chunks_conf` to the `ON CONFLICT ... DO UPDATE SET`, and add the value to the tuple:
```python
                json.dumps(chunks_conf) if chunks_conf is not None else None,
```
(NULL when absent — distinguishes "not captured" from "captured empty"; the other JSON columns always-array pattern is deliberately NOT used here.)

(c) `_row` — tolerant parse (mirrors the `track_languages` migration-011 tolerance):
```python
        try:
            raw_cc = r["chunks_conf"]
            chunks_conf = self._json_list(raw_cc) if raw_cc is not None else None
        except (IndexError, KeyError):
            chunks_conf = None
```
and pass `chunks_conf=chunks_conf` into the `AuditFinding(...)` constructor.

- [ ] **Step 5: Run to verify it passes**

Run: `python -m pytest tests/test_audio_audit_store.py -q`
Expected: PASS (new tests + all existing store tests still green).

- [ ] **Step 6: Lint + commit**
```bash
ruff check src/subarr/audio_audit_store.py tests/test_audio_audit_store.py
ruff format src/subarr/audio_audit_store.py tests/test_audio_audit_store.py
git add src/subarr/migrations/029_audio_audit_chunks_conf.sql src/subarr/audio_audit_store.py tests/test_audio_audit_store.py
git commit -m "feat(#407): persist chunks_conf column on the audio-lang audit store"
```

---

## Task 2: `_audit_one` captures `chunks_conf`

**Files:**
- Modify: `src/subarr/audio_audit.py`
- Test: `tests/test_audio_audit.py`

- [ ] **Step 1: Write the failing test** — add to the audit-walker test file. Reuse its existing harness (fake subgen returning a robust-detect response + in-memory stores). The key assertion: after `_audit_one` (or a one-file walk), the stored `AuditFinding.chunks_conf` matches the per-chunk data the fake subgen returned.

```python
def test_audit_persists_chunks_conf(...):
    # fake subgen.detect_language_robust returns a robust response whose chunks
    # carry (language, probability); parse_robust_detect -> chunks_conf.
    # Run _audit_one for one canonical path, then:
    f = audit_store.get(canonical)
    assert f.chunks_conf == [["gl", 0.94], ["es", 0.88], ["fr", 0.71]]
```

Model the fake subgen response on the shape `parse_robust_detect` expects (see `arena.py` `parse_robust_detect` — chunks with a `probability` field). Reuse whatever fixture the existing `_audit_one` / walker tests already use; if a test already drives `_audit_one`, copy its setup and add the assertion.

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_audio_audit.py -q -k chunks_conf`
Expected: FAIL — stored `chunks_conf` is `None` (not yet wired).

- [ ] **Step 3: Implement** — in `src/subarr/audio_audit.py`, the `self._store.upsert(...)` call in `_audit_one` (the one that passes `languages_heard=...`), add:
```python
            chunks_conf=(detect or {}).get("chunks_conf"),
```
Nothing else in the audit path changes; classification is untouched.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_audio_audit.py -q`
Expected: PASS (new test + existing audit tests, classification unchanged).

- [ ] **Step 5: Lint + commit**
```bash
ruff check src/subarr/audio_audit.py tests/test_audio_audit.py
ruff format src/subarr/audio_audit.py tests/test_audio_audit.py
git add src/subarr/audio_audit.py tests/test_audio_audit.py
git commit -m "feat(#407): capture chunks_conf during the audio-lang audit walk"
```

---

## Task 3: Read-only T-sweep inspector

**Files:**
- Create: `src/subarr/multilang_tune.py`
- Create: `scripts/multilang_tune.py`
- Test: `tests/test_multilang_tune.py`

- [ ] **Step 1: Write the failing test** `tests/test_multilang_tune.py`:

```python
from subarr.multilang_tune import multilang_sweep, prob_distribution, t_grid, format_report


def test_sweep_multilingual_count_monotonic_non_increasing_in_t():
    # A clean 3-confident-lang file, a single-lang file, and a low-conf/confused file.
    corpus = [
        [["gl", 0.94], ["es", 0.88], ["fr", 0.71]],   # multilingual until T passes 0.71
        [["en", 0.97], ["en", 0.95]],                 # single
        [["de", 0.20], ["it", 0.18]],                 # confused (never >= a real T)
    ]
    grid = [0.3, 0.5, 0.75, 0.9]
    rows = multilang_sweep(corpus, grid)
    counts = [r.multilingual for r in rows]
    assert counts == sorted(counts, reverse=True)          # non-increasing in T
    assert rows[0].multilingual == 1                       # T=0.3: the gl/es/fr file
    assert rows[grid.index(0.75)].multilingual == 0        # T=0.75: fr(0.71) drops out -> single


def test_prob_distribution_quantiles():
    corpus = [[["gl", 0.9], ["es", 0.8]], [["en", 0.6], ["en", 0.4]]]
    d = prob_distribution(corpus)
    assert d["n"] == 4 and d["min"] == 0.4 and d["max"] == 0.9


def test_report_renders_and_handles_empty_corpus():
    assert "empty" in format_report(multilang_sweep([], t_grid()), prob_distribution([])).lower()
    txt = format_report(multilang_sweep([[["gl", 0.9], ["es", 0.8]]], [0.5]), prob_distribution([[["gl", 0.9], ["es", 0.8]]]))
    assert "T=0.50" in txt and "multilingual=1" in txt
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_multilang_tune.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'subarr.multilang_tune'`.

- [ ] **Step 3: Implement `src/subarr/multilang_tune.py`**

```python
"""#407 off-app tuning aid: sweep the multilingual chunk-confidence threshold T
across the accrued per-chunk-probability corpus and report how the multilingual
classification and the probability distribution respond. Read-only, CPU-only —
never runs detection, only reads stored `chunks_conf`. Reuses
`classify_high_conf_langs` so the corpus reader and the live classifier can never
diverge. The actual T-bake is Part B (data-gated on accrued multilingual positives)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from statistics import median

from .multilang import classify_high_conf_langs

# A corpus entry is one file's chunks_conf: [[lang, prob], ...] (JSON-decoded → lists).
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

    return {"n": len(probs), "min": probs[0], "p25": q(0.25), "median": median(probs), "p75": q(0.75), "max": probs[-1]}


def corpus_from_audit_store(db_path: str, *, limit: int | None = None) -> list[ChunksConf]:
    """Read every non-NULL chunks_conf from audio_lang_audit. Read-only; opens its
    own connection (off-app tool). Malformed JSON / empty lists are skipped."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT chunks_conf FROM audio_lang_audit WHERE chunks_conf IS NOT NULL").fetchall()
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
        lines.append("corpus: empty (no chunks_conf captured yet — run the audio-lang audit first)")
    for r in rows:
        lines.append(f"T={r.t:.2f} | multilingual={r.multilingual} single={r.single} confused={r.confused}")
    return "\n".join(lines)
```

- [ ] **Step 4: Implement the CLI `scripts/multilang_tune.py`** (mirror `scripts/retime_tune.py`'s bootstrap/imports — check that file for the exact `sys.path`/arg style and match it):

```python
#!/usr/bin/env python3
"""#407 read-only CLI: sweep the multilingual chunk-confidence threshold T over
the accrued audio-lang audit corpus. Never runs detection."""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from subarr.multilang_tune import corpus_from_audit_store, format_report, multilang_sweep, prob_distribution, t_grid


def main() -> None:
    ap = argparse.ArgumentParser(description="Sweep multilingual chunk-confidence threshold T (read-only).")
    ap.add_argument("--db", default=os.environ.get("SUBARR_DB_PATH", "/data/subarr.db"))
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    corpus = corpus_from_audit_store(args.db, limit=args.limit)
    print(format_report(multilang_sweep(corpus, t_grid()), prob_distribution(corpus)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run to verify it passes**

Run: `python -m pytest tests/test_multilang_tune.py -q`
Expected: PASS (3 tests).

- [ ] **Step 6: Lint + commit**
```bash
ruff check src/subarr/multilang_tune.py tests/test_multilang_tune.py scripts/multilang_tune.py
ruff format src/subarr/multilang_tune.py tests/test_multilang_tune.py scripts/multilang_tune.py
git add src/subarr/multilang_tune.py scripts/multilang_tune.py tests/test_multilang_tune.py
git commit -m "feat(#407): read-only multilingual T-sweep inspector + CLI"
```

---

## Task 4: Full suite + wrap

- [ ] **Step 1:** `python -m pytest -q` — all green (new tests + no regression; classification untouched so multilingual/audit tests unchanged). Investigate any full-suite-only flake against the conftest module-reload gotcha (`reference_subarr-test-module-reload`).
- [ ] **Step 2:** `ruff check src/ tests/ && ruff format --check src/ tests/` — clean.
- [ ] **Step 3:** Sanity-run the CLI against the dev DB (read-only, no GPU) to confirm it renders — expected output today is the "empty (no chunks_conf captured yet)" corpus line, since capture only starts accruing after this ships and the audit re-runs. Record the output inline; nothing to commit.
- [ ] **Step 4:** Use superpowers:finishing-a-development-branch (open a PR; Tier-1 — additive column + read-only tool, no behavior change).

---

## Self-Review

- **Spec coverage:** capture at audit path → Task 2; additive nullable column → Task 1 (migration 029); store learns it → Task 1; read-only inspector reusing `classify_high_conf_langs` → Task 3; no behavior change (T still 0.5) → Tasks 2/4; T-bake deferred (Part B) → not in plan by design.
- **Placeholder scan:** none — real migration, column, function names, and the exact `_audit_one`/`upsert` edit points.
- **Type consistency:** `chunks_conf` is `list | None` on the dataclass/upsert; stored as JSON (`None`→NULL); read back as JSON lists (`[[lang, prob]]`), which `classify_high_conf_langs` consumes via `for lang, prob in ...` (works on lists); `SweepRow`/`multilang_sweep`/`format_report` share the `ChunksConf = list` shape.

---

## Execution Handoff

Two options: **(1) Subagent-Driven (recommended)** — fresh subagent per task, two-stage review between tasks; **(2) Inline Execution**. Recommend option 1, consistent with the slice-2 precedent.
