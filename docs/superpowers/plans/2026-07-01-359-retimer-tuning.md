# #359 Re-timer Tuning Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an off-app harness that sweeps `RetimeParams` across subarr's real subgen-produced subtitle corpus (from the `subs_generated` ledger) and reports ranked readability deltas, so we can pick proven defaults.

**Architecture:** Pure core + adapters in an importable `src/subarr/retime_tune.py`; a thin CLI in `scripts/retime_tune.py`. The core `retime_sweep` applies `retime_srt` across a corpus for each param combo and pools readability metrics via `subtitle_readability`. The ledger adapter resolves each completed `subs_generated` path to its **original** subgen `.en.srt` (excluding subsyncarr variants) and skips Bazarr-replaced files via an `aftercare_results.cue_count` guard. Read-only — never writes subs or the DB.

**Tech Stack:** Python 3.11, sqlite3 (stdlib), pytest, ruff. Spec: `docs/superpowers/specs/2026-07-01-359-retimer-tuning-design.md`.

**Branch:** `feat/359-retimer-tuning` (already created, spec committed).

**Conventions:**
- TDD: failing test → run red → minimal impl → run green → commit. Run the specific test file until the final task.
- Reuse `subtitle_readability` (`Cue`, `parse_srt`, constants `MAX_CPS=20.0`/`CRITICAL_CPS=25.0`/`MIN_DURATION_S=0.833`/`MAX_DURATION_S=7.0`) and `subtitle_retime` (`RetimeParams`, `retime_srt`). Do NOT redefine CPS/char logic.
- Ruff hook strips a just-added top-level import if unused in the same edit — add usage first or import function-locally.
- Commit footer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 1: Pure sweep core — `retime_sweep` + `SweepRow` + `param_grid`

**Files:**
- Create: `src/subarr/retime_tune.py`
- Test: `tests/test_retime_tune.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
"""#359: pure re-timer parameter sweep over an SRT corpus."""
from __future__ import annotations

from subarr.retime_tune import SweepRow, param_grid, retime_sweep
from subarr.subtitle_retime import RetimeParams

# One hot sub (40 cps cue + a micro-cue) and one comfortable sub.
_HOT = (
    "1\n00:00:00,000 --> 00:00:02,000\n"
    "This is a very long translated line that crams far too many characters\n\n"
    "2\n00:00:20,000 --> 00:00:20,300\nhi\n"
)
_CALM = "1\n00:00:00,000 --> 00:00:03,000\nHello there.\n\n2\n00:00:04,000 --> 00:00:07,000\nGeneral Kenobi.\n"


def test_param_grid_is_target_cps_x_min_cue():
    grid = param_grid()
    assert len(grid) == 9
    assert RetimeParams(target_cps=17.0, min_cue_ms=1000, min_gap_ms=100, max_cue_ms=7000) in grid
    assert all(p.min_gap_ms == 100 and p.max_cue_ms == 7000 for p in grid)


def test_sweep_has_baseline_first_then_one_row_per_combo():
    rows = retime_sweep([_HOT, _CALM], param_grid())
    assert rows[0].params is None  # baseline (no re-timing)
    assert len(rows) == 1 + len(param_grid())
    assert all(r.subs == 2 for r in rows)


def test_sweep_reduces_critical_cps_and_micro_cues_vs_baseline():
    rows = retime_sweep([_HOT, _CALM], param_grid())
    baseline = rows[0]
    treated = [r for r in rows if r.params is not None]
    # every combo should reduce (or hold) %over-critical and micro-cues, and add screen time.
    assert any(r.pct_over_critical < baseline.pct_over_critical for r in treated)
    assert all(r.micro_cues <= baseline.micro_cues for r in treated)
    assert all(r.too_long <= baseline.too_long for r in treated)  # cap prevents over-long
    changed = [r for r in treated if r.subs_changed > 0]
    assert changed and all(r.mean_added_ms > 0 for r in changed)


def test_sweep_leaves_comfortable_only_corpus_essentially_unchanged():
    rows = retime_sweep([_CALM], param_grid())
    baseline = rows[0]
    for r in rows[1:]:
        assert r.subs_changed == 0
        assert r.median_cps == baseline.median_cps


def test_sweep_lower_target_cps_reduces_median_more():
    grid = [
        RetimeParams(target_cps=20.0, min_cue_ms=1000, min_gap_ms=100, max_cue_ms=7000),
        RetimeParams(target_cps=15.0, min_cue_ms=1000, min_gap_ms=100, max_cue_ms=7000),
    ]
    rows = retime_sweep([_HOT], grid)
    at20 = next(r for r in rows if r.params and r.params.target_cps == 20.0)
    at15 = next(r for r in rows if r.params and r.params.target_cps == 15.0)
    assert at15.median_cps <= at20.median_cps  # aim lower → extend more → lower CPS
```

- [ ] **Step 2: Run red**

Run: `python -m pytest tests/test_retime_tune.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'subarr.retime_tune'`.

- [ ] **Step 3: Implement the core**

`src/subarr/retime_tune.py`:
```python
"""#359 off-app tuning: sweep RetimeParams across a subtitle corpus and report
pooled readability deltas. Pure + deterministic core; corpus adapters + CLI
elsewhere. The manual bootstrap of the federated-tuning loop (#124)."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, median

from .subtitle_readability import (
    CRITICAL_CPS,
    MAX_CPS,
    MAX_DURATION_S,
    MIN_DURATION_S,
    Cue,
    parse_srt,
)
from .subtitle_retime import RetimeParams, retime_srt


def param_grid() -> list[RetimeParams]:
    """The sweep grid: target_cps x min_cue_ms; gap/max held at Netflix values."""
    return [
        RetimeParams(target_cps=tc, min_cue_ms=mc, min_gap_ms=100, max_cue_ms=7000)
        for tc in (15.0, 17.0, 20.0)
        for mc in (833, 1000, 1200)
    ]


@dataclass(frozen=True)
class SweepRow:
    params: RetimeParams | None  # None = baseline (no re-timing)
    subs: int
    subs_changed: int
    median_cps: float
    pct_over_critical: float  # cues > CRITICAL_CPS (25)
    pct_over_comfortable: float  # cues > MAX_CPS (20)
    micro_cues: int  # cues < MIN_DURATION_S
    too_long: int  # cues > MAX_DURATION_S
    mean_added_ms: float  # mean screen-time added per sub


def _metrics(cues: list[Cue]) -> dict:
    live = [c for c in cues if c.duration_s > 0]
    cpses = [c.cps for c in live]
    n = len(cpses) or 1
    return {
        "median_cps": median(cpses) if cpses else 0.0,
        "pct_over_critical": sum(1 for x in cpses if x > CRITICAL_CPS) / n,
        "pct_over_comfortable": sum(1 for x in cpses if x > MAX_CPS) / n,
        "micro_cues": sum(1 for c in live if c.duration_s < MIN_DURATION_S),
        "too_long": sum(1 for c in live if c.duration_s > MAX_DURATION_S),
    }


def _dur_ms(cues: list[Cue]) -> int:
    return sum(c.end_ms - c.start_ms for c in cues)


def retime_sweep(texts: list[str], grid: list[RetimeParams]) -> list[SweepRow]:
    """Baseline row (params=None) first, then one row per grid combo. Metrics are
    pooled across all corpus cues; mean_added_ms is averaged per sub."""
    parsed = [parse_srt(t) for t in texts]
    before = [c for cues in parsed for c in cues]
    bm = _metrics(before)
    rows = [SweepRow(None, len(texts), 0, bm["median_cps"], bm["pct_over_critical"],
                     bm["pct_over_comfortable"], bm["micro_cues"], bm["too_long"], 0.0)]
    for params in grid:
        after: list[Cue] = []
        added: list[int] = []
        changed = 0
        for text, b in zip(texts, parsed):
            new = retime_srt(text, params)
            a = parse_srt(new)
            after.extend(a)
            if new != text:
                changed += 1
            added.append(_dur_ms(a) - _dur_ms(b))
        m = _metrics(after)
        rows.append(SweepRow(params, len(texts), changed, m["median_cps"], m["pct_over_critical"],
                             m["pct_over_comfortable"], m["micro_cues"], m["too_long"],
                             mean(added) if added else 0.0))
    return rows
```

- [ ] **Step 4: Run green**

Run: `python -m pytest tests/test_retime_tune.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Lint + commit**

Run: `python -m ruff check src/subarr/retime_tune.py tests/test_retime_tune.py`
```bash
git add src/subarr/retime_tune.py tests/test_retime_tune.py
git commit -m "feat(#359): retime_sweep — pure param-sweep core over an SRT corpus

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Corpus adapters — `corpus_from_dir` + `corpus_from_ledger`

**Files:**
- Modify: `src/subarr/retime_tune.py` (add the adapters + sidecar helpers)
- Test: append to `tests/test_retime_tune.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_corpus_from_dir_reads_srts_and_skips_sync_variants(tmp_path):
    from subarr.retime_tune import corpus_from_dir

    (tmp_path / "a.en.srt").write_text(_CALM, encoding="utf-8")
    (tmp_path / "a.en.ffsubsync.srt").write_text(_CALM, encoding="utf-8")  # subsyncarr variant
    (tmp_path / "a.en.alass.srt").write_text(_CALM, encoding="utf-8")
    (tmp_path / "junk.txt").write_text("nope", encoding="utf-8")
    corpus = corpus_from_dir(str(tmp_path))
    names = sorted(n for n, _ in corpus)
    assert names == ["a.en.srt"]  # variants + non-srt excluded


def test_original_sidecar_prefers_plain_and_excludes_engine_suffix(tmp_path):
    from subarr.retime_tune import _original_sidecar

    video = tmp_path / "Show - S01E01.mkv"
    video.write_text("x")
    (tmp_path / "Show - S01E01.en.srt").write_text(_CALM, encoding="utf-8")
    (tmp_path / "Show - S01E01.en.ffsubsync.srt").write_text(_CALM, encoding="utf-8")
    got = _original_sidecar(video)
    assert got is not None and got.name == "Show - S01E01.en.srt"


def test_corpus_from_ledger_gathers_original_and_guards_replaced(tmp_path):
    import sqlite3

    from subarr.retime_tune import corpus_from_ledger

    # temp DB with the two tables we read.
    db = tmp_path / "s.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE subs_generated (canonical_path TEXT, completed_at REAL)")
    conn.execute("CREATE TABLE aftercare_results (id INTEGER PRIMARY KEY, canonical_path TEXT, cue_count INTEGER)")
    conn.execute("INSERT INTO subs_generated VALUES (?, ?)", ("TV/Keep/ep.mkv", 123.0))
    conn.execute("INSERT INTO subs_generated VALUES (?, ?)", ("TV/Replaced/ep.mkv", 124.0))
    conn.execute("INSERT INTO subs_generated VALUES (?, ?)", ("TV/Pending/ep.mkv", None))  # not completed
    conn.commit()

    keep_dir = tmp_path / "keep"
    keep_dir.mkdir()
    (keep_dir / "ep.en.srt").write_text(_CALM, encoding="utf-8")  # 2 cues
    repl_dir = tmp_path / "repl"
    repl_dir.mkdir()
    (repl_dir / "ep.en.srt").write_text(_CALM, encoding="utf-8")  # 2 cues on disk...
    # aftercare recorded 9 cues when subarr made it → on-disk (2) mismatches → replaced.
    conn.execute("INSERT INTO aftercare_results (canonical_path, cue_count) VALUES (?, ?)", ("TV/Replaced/ep.mkv", 9))
    conn.execute("INSERT INTO aftercare_results (canonical_path, cue_count) VALUES (?, ?)", ("TV/Keep/ep.mkv", 2))
    conn.commit()
    conn.close()

    def _resolve(canon: str):
        return {"TV/Keep/ep.mkv": keep_dir / "ep.mkv", "TV/Replaced/ep.mkv": repl_dir / "ep.mkv"}.get(canon)

    corpus = corpus_from_ledger(str(db), resolve=_resolve)
    paths = [p for p, _ in corpus]
    assert paths == ["TV/Keep/ep.mkv"]  # completed + cue_count matches; Replaced skipped, Pending excluded
```

- [ ] **Step 2: Run red**

Run: `python -m pytest tests/test_retime_tune.py -k "corpus or sidecar" -q`
Expected: FAIL — `ImportError`/`AttributeError` (adapters not defined).

- [ ] **Step 3: Implement the adapters**

Append to `src/subarr/retime_tune.py`:
```python
import sqlite3
from pathlib import Path

# subsyncarr writes engine-suffixed re-syncs of an existing sub; those are
# timing-shifted derivatives with identical CPS — never the subgen original.
_SYNC_MARKERS = ("ffsubsync", "alass", "autosubsync", "subsync")


def _is_sync_variant(srt_name: str) -> bool:
    return any(m in srt_name.lower() for m in _SYNC_MARKERS)


def _original_sidecar(video_full: Path) -> Path | None:
    """The subgen-original .srt next to the video: prefer <stem>.en.srt, else the
    first <stem>*.srt that is NOT a subsyncarr engine variant."""
    try:
        stem, parent = video_full.stem, video_full.parent
        preferred = parent / f"{stem}.en.srt"
        if preferred.exists():
            return preferred
        for p in sorted(parent.glob(f"{stem}*.srt")):
            if not _is_sync_variant(p.name):
                return p
    except OSError:
        pass
    return None


def corpus_from_dir(path: str) -> list[tuple[str, str]]:
    """Every *.srt in a folder (excluding subsyncarr variants + unparseable)."""
    out: list[tuple[str, str]] = []
    for p in sorted(Path(path).glob("*.srt")):
        if _is_sync_variant(p.name):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if parse_srt(text):
            out.append((p.name, text))
    return out


def _default_resolve(canonical_path: str) -> "Path | None":
    from .paths import PathOutsideRootError, canonical_to_fs

    try:
        return canonical_to_fs(canonical_path)
    except (OSError, PathOutsideRootError):
        return None


def corpus_from_ledger(db_path: str, *, resolve=None, cue_tol: int = 1) -> list[tuple[str, str]]:
    """Corpus from the subs_generated ledger: each completed row → its original
    subgen sidecar. Skips files whose on-disk cue_count differs from the recorded
    aftercare cue_count by more than cue_tol (a Bazarr provider sub replaced it)."""
    resolve = resolve or _default_resolve
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT canonical_path FROM subs_generated WHERE completed_at IS NOT NULL"
        ).fetchall()
        out: list[tuple[str, str]] = []
        for (canon,) in rows:
            video = resolve(canon)
            if not video:
                continue
            srt = _original_sidecar(video)
            if not srt or not srt.exists():
                continue
            try:
                text = srt.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            cues = parse_srt(text)
            if not cues:
                continue
            rec = conn.execute(
                "SELECT cue_count FROM aftercare_results WHERE canonical_path = ? ORDER BY id DESC LIMIT 1",
                (canon,),
            ).fetchone()
            if rec and rec[0] is not None and abs(len(cues) - int(rec[0])) > cue_tol:
                continue  # replaced (likely a Bazarr provider download)
            out.append((canon, text))
        return out
    finally:
        conn.close()
```
(The `import sqlite3` / `from pathlib import Path` go at the top of the file with the other imports — move them up when you add them so ruff is happy.)

- [ ] **Step 4: Run green**

Run: `python -m pytest tests/test_retime_tune.py -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Lint + commit**

Run: `python -m ruff check src/subarr/retime_tune.py tests/test_retime_tune.py`
```bash
git add src/subarr/retime_tune.py tests/test_retime_tune.py
git commit -m "feat(#359): corpus adapters — ledger (subsyncarr-excluded, aftercare-guarded) + dir

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: CLI — `scripts/retime_tune.py`

**Files:**
- Create: `scripts/retime_tune.py`
- Test: append a smoke test to `tests/test_retime_tune.py`

- [ ] **Step 1: Write the failing test**

```python
def test_format_report_ranks_and_shows_baseline():
    from subarr.retime_tune import format_report, retime_sweep

    rows = retime_sweep([_HOT, _CALM], param_grid())
    report = format_report(rows)
    assert "baseline" in report.lower()
    assert "median_cps" in report or "median" in report.lower()
    # a line per row (baseline + combos)
    assert report.count("target_cps=") >= 1
```

- [ ] **Step 2: Run red**

Run: `python -m pytest tests/test_retime_tune.py::test_format_report_ranks_and_shows_baseline -q`
Expected: FAIL — `format_report` not defined.

- [ ] **Step 3: Add `format_report` to the module + the CLI**

Append to `src/subarr/retime_tune.py`:
```python
def _rank_key(r: "SweepRow") -> tuple:
    # best = lowest %over-critical, then lowest median CPS, then least screen-time added.
    return (r.pct_over_critical, r.median_cps, r.mean_added_ms)


def format_report(rows: list[SweepRow]) -> str:
    baseline = next((r for r in rows if r.params is None), None)
    treated = sorted((r for r in rows if r.params is not None), key=_rank_key)
    lines = ["# re-timer sweep (ranked; baseline = no re-timing)"]
    if baseline:
        lines.append(
            f"baseline: subs={baseline.subs} median_cps={baseline.median_cps:.1f} "
            f"%>25={baseline.pct_over_critical:.1%} %>20={baseline.pct_over_comfortable:.1%} "
            f"micro={baseline.micro_cues} too_long={baseline.too_long}"
        )
    for r in treated:
        p = r.params
        lines.append(
            f"target_cps={p.target_cps:<4} min_cue={p.min_cue_ms:<5} | "
            f"median_cps={r.median_cps:.1f} %>25={r.pct_over_critical:.1%} "
            f"%>20={r.pct_over_comfortable:.1%} micro={r.micro_cues} too_long={r.too_long} "
            f"changed={r.subs_changed}/{r.subs} +{r.mean_added_ms:.0f}ms/sub"
        )
    return "\n".join(lines)
```

Create `scripts/retime_tune.py`:
```python
#!/usr/bin/env python3
"""#359 off-app tuning CLI: sweep RetimeParams across subarr's subgen corpus.

  python scripts/retime_tune.py --db /data/subarr.db      # from the subs_generated ledger
  python scripts/retime_tune.py --dir /path/to/srts       # from a folder of .srt files

Read-only: never writes subtitles or the DB."""

import argparse
import sys

from subarr.retime_tune import corpus_from_dir, corpus_from_ledger, format_report, param_grid, retime_sweep


def main() -> int:
    ap = argparse.ArgumentParser(description="Sweep RetimeParams across an SRT corpus.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--db", help="subarr DB path; corpus = completed subs_generated rows")
    src.add_argument("--dir", help="folder of .srt files (bypasses the ledger)")
    ap.add_argument("--limit", type=int, default=0, help="cap corpus size (0 = all)")
    args = ap.parse_args()

    corpus = corpus_from_dir(args.dir) if args.dir else corpus_from_ledger(args.db)
    if args.limit:
        corpus = corpus[: args.limit]
    if not corpus:
        print("empty corpus — nothing to sweep", file=sys.stderr)
        return 1
    texts = [t for _, t in corpus]
    print(f"corpus: {len(texts)} subs\n")
    print(format_report(retime_sweep(texts, param_grid())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run green + a CLI smoke check**

Run: `python -m pytest tests/test_retime_tune.py -q`
Expected: PASS (9 tests).
Then a real CLI smoke test against a temp dir:
```bash
mkdir -p /tmp/rt && printf '1\n00:00:00,000 --> 00:00:02,000\n%s\n' "$(python -c "print('x'*80)")" > /tmp/rt/a.en.srt
python scripts/retime_tune.py --dir /tmp/rt
```
Expected: prints `corpus: 1 subs` + the ranked table with a baseline line.

- [ ] **Step 5: Lint + commit**

Run: `python -m ruff check src/subarr/retime_tune.py scripts/retime_tune.py tests/test_retime_tune.py`
```bash
git add src/subarr/retime_tune.py scripts/retime_tune.py tests/test_retime_tune.py
git commit -m "feat(#359): retime_tune CLI + ranked report

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Full verification + PR

**Files:** none (verification).

- [ ] **Step 1: Full suite + ruff**

Run: `python -m pytest -q && python -m ruff check src/subarr/ tests/ scripts/`
Expected: all pass (prior total + 9 new); All checks passed. Investigate any failure.

- [ ] **Step 2: Push + PR**

```bash
git push -u origin feat/359-retimer-tuning
gh pr create --title "#359: re-timer tuning harness (provenance-fed param sweep)" \
  --body "Third #359 slice — the off-app tuning harness. \`src/subarr/retime_tune.py\` (pure \`retime_sweep\` + corpus adapters) + \`scripts/retime_tune.py\` CLI. Corpus = the \`subs_generated\` ledger resolved to original subgen \`.en.srt\` (subsyncarr variants excluded; Bazarr-replaced files skipped via the aftercare cue_count guard), plus a \`--dir\` fallback. Read-only. Reports ranked readability deltas (median CPS, %>25/%>20, micro-cues, too_long introduced, screen-time added) so we can pick proven \`RetimeParams\`. The bake (defaults + flip the flag) is a deferred follow-up on the recommendation. Spec: docs/superpowers/specs/2026-07-01-359-retimer-tuning-design.md. <N> tests, ruff clean."
```

- [ ] **Step 3: Merge (Tier-0/1 — read-only off-app tool, pure core)**

```bash
gh pr merge --squash --admin --delete-branch
git checkout main && git pull --ff-only
```

---

## Post-merge (controller, not a code task): run + recommend

After merge, run `scripts/retime_tune.py --db <subarr-next DB> ` inside the subarr-next container (DB + media mounted), over the live ~2112-sub corpus. Read the ranked table for the knee — the params giving the biggest drop in `%>25` and median CPS before `too_long`/`+ms/sub` climb into over-extension. Bring the user a recommended `RetimeParams` with the numbers. The bake (update defaults + flip `SUBARR_RETIME_ENABLED` default on) is a separate tiny PR on their sign-off.

## Self-Review notes (for the executor)

- **Spec coverage:** Task 1 = `retime_sweep`/`SweepRow`/grid + metrics (spec §Components.1, §Param grid, §Testing sweep); Task 2 = `corpus_from_dir`/`corpus_from_ledger` incl. subsyncarr exclusion + aftercare guard (spec §Corpus gathering, §Components.2, §Testing corpus); Task 3 = CLI + `format_report` (spec §Components.3); Task 4 = verify + ship. The real run + bake are post-merge/deferred — correct.
- **Type consistency:** `RetimeParams(target_cps,min_cue_ms,min_gap_ms,max_cue_ms)`, `SweepRow`, `retime_sweep(texts,grid)`, `param_grid()`, `corpus_from_dir(path)`, `corpus_from_ledger(db_path, *, resolve, cue_tol)`, `_original_sidecar(video_full)`, `format_report(rows)` — consistent across tasks and match `subtitle_retime`/`subtitle_readability`.
- **Watch item:** `corpus_from_ledger`'s default resolver imports `canonical_to_fs` (needs `settings.media_root`) — only exercised in the real run (container env); the unit test injects `resolve=` so it never touches settings.
