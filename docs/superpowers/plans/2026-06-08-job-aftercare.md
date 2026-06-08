# Job Aftercare (Track A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On every completed everyday transcription job, run subarr's existing readability + failure-mode judges on the produced `.srt`, store the result, and surface flagged jobs in a Review page + header pill + dashboard panel.

**Architecture:** A pure evaluation module (`aftercare.py`) wraps the existing `tournament.score_entrant`; a SQLite store (`aftercare_store.py`, migration 017) persists one row per completed job; `CompletionWatcher.complete_entry` calls the judge best-effort (sync, CPU-only, never blocks completion); a router exposes pending-count/results/acknowledge; the frontend mirrors the Health page. Hybrid scope: store everything, surface flagged-only by default. The composite measures failure-absence + readability, NOT accuracy — the UI leads with flags, never a confident positive grade (that's L3/#123).

**Tech Stack:** Python 3.12 / FastAPI / SQLite (hand-rolled migrations) / vanilla React 18 JSX bundles (esbuild). Run tests with `PYTHONPATH=src` (a stale editable install shadows the source otherwise). On this machine, run pytest from PowerShell: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest ...`.

---

## File Structure

- **Create** `src/subarr/aftercare.py` — pure: flag-bar constants, `AftercareEvaluation` dataclass, `_is_flagged(card)`, `evaluate_subtitle(srt_text)`. Wraps `tournament.score_entrant`.
- **Create** `src/subarr/aftercare_store.py` — `AfterCareStore` (record / pending_count / list_results / get / mark_reviewed). Mirrors `error_store.py`/`task_health.py`.
- **Create** `src/subarr/migrations/017_aftercare_results.sql` — the `aftercare_results` table.
- **Create** `src/subarr/routers/aftercare.py` — `GET /api/aftercare/pending`, `GET /api/aftercare/results`, `POST /api/aftercare/{id}/acknowledge`.
- **Modify** `src/subarr/completion_watcher.py` — add `aftercare_store` param + `_run_aftercare(entry)` called inside `complete_entry`.
- **Modify** `src/subarr/app.py` — construct `app.state.aftercare`, pass to `CompletionWatcher`, include the router, register the `/aftercare` screen, close store on shutdown.
- **Create** `src/subarr/static/v1/home-hifi/aftercare.jsx` + `entries/aftercare.entry.jsx` + `aftercare.html` — the page (mirrors `health.jsx`).
- **Modify** `src/subarr/static/v1/home-hifi/chrome.jsx` — pending fetch + header pill + Operations rail item.
- **Modify** `src/subarr/static/v1/home-hifi/dashboard.jsx` — `AfterCarePanel` (renders only when count>0).

**Migration number:** main's highest is `015`; `016` is reserved by the open #159 PR. This plan uses **`017`** so it coexists regardless of merge order. Confirm with `ls src/subarr/migrations/` before Task 1.

---

## Task 1: Migration 017 — `aftercare_results` table

**Files:**
- Create: `src/subarr/migrations/017_aftercare_results.sql`
- Test: `tests/test_aftercare_store.py`

- [ ] **Step 1: Write the migration SQL**

Create `src/subarr/migrations/017_aftercare_results.sql`:

```sql
-- 017_aftercare_results.sql
--
-- #156 Track A: one row per completed transcription job, carrying subarr's own
-- quality judging of the produced .srt (readability + failure-mode signals).
-- Stores EVERY job (foundation for #95 passive tuning); the UI surfaces flagged
-- ones. `reviewed_at` NULL = pending review. Keyed by file path; a requeue
-- appends a new row (history), the list view shows the latest per path.

CREATE TABLE IF NOT EXISTS aftercare_results (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_path   TEXT NOT NULL,
    completed_at     REAL NOT NULL,
    composite        REAL NOT NULL,
    cue_count        INTEGER NOT NULL,
    flagged          INTEGER NOT NULL,          -- 0/1
    readability_json TEXT,                      -- ReadabilityReport.to_dict()
    signals_json     TEXT,                      -- score_entrant signals dict
    source           TEXT,                      -- 'subgenscan' | 'gaps' | 'manual' | ...
    reviewed_at      REAL,                      -- NULL = pending
    created_at       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_aftercare_pending
    ON aftercare_results (flagged, reviewed_at, completed_at);
CREATE INDEX IF NOT EXISTS idx_aftercare_path
    ON aftercare_results (canonical_path, completed_at);
```

- [ ] **Step 2: Write a failing test that the table exists after migration**

Create `tests/test_aftercare_store.py`:

```python
"""#156 aftercare store + migration."""
from __future__ import annotations

import time

import pytest


def _migrated_db(tmp_path):
    from subarr.migrate import run_migrations
    db = tmp_path / "a.db"
    run_migrations(db)
    return db


def test_migration_creates_aftercare_table(tmp_path):
    import sqlite3
    db = _migrated_db(tmp_path)
    conn = sqlite3.connect(str(db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(aftercare_results)")}
    assert {"id", "canonical_path", "completed_at", "composite", "cue_count",
            "flagged", "readability_json", "signals_json", "source",
            "reviewed_at", "created_at"} <= cols
```

- [ ] **Step 3: Run the test to verify it passes (migration auto-discovered)**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/test_aftercare_store.py::test_migration_creates_aftercare_table -v`
Expected: PASS (the runner auto-discovers `*.sql` in `migrations/`).

- [ ] **Step 4: Commit**

```bash
git add src/subarr/migrations/017_aftercare_results.sql tests/test_aftercare_store.py
git commit -m "feat(#156): aftercare_results table (migration 017)"
```

---

## Task 2: `aftercare.py` — evaluation + flag logic (pure)

**Files:**
- Create: `src/subarr/aftercare.py`
- Test: `tests/test_aftercare.py`

- [ ] **Step 1: Write failing tests for the flag bar + evaluation**

Create `tests/test_aftercare.py`:

```python
"""#156 aftercare evaluation + flag bar (pure)."""
from __future__ import annotations

from subarr.aftercare import AftercareEvaluation, evaluate_subtitle

_CLEAN = (
    "1\n00:00:01,000 --> 00:00:03,000\nHello there.\n\n"
    "2\n00:00:04,000 --> 00:00:06,500\nHow are you today?\n\n"
)
_LOOPING = "".join(
    f"{i}\n00:00:{i:02d},000 --> 00:00:{i:02d},800\nThanks for watching!\n\n"
    for i in range(1, 11)
)


def test_clean_subtitle_not_flagged():
    ev = evaluate_subtitle(_CLEAN)
    assert isinstance(ev, AftercareEvaluation)
    assert ev.flagged is False
    assert ev.cue_count == 2
    assert ev.composite > 65


def test_canned_hallucination_flagged():
    ev = evaluate_subtitle(_LOOPING)   # repeats + canned "Thanks for watching!"
    assert ev.flagged is True
    assert (ev.signals or {}).get("canned_phrase_hits", 0) > 0


def test_empty_subtitle_flagged_and_disqualified():
    ev = evaluate_subtitle("")
    assert ev.flagged is True
    assert ev.cue_count == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/test_aftercare.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'subarr.aftercare'`.

- [ ] **Step 3: Implement `aftercare.py`**

Create `src/subarr/aftercare.py`:

```python
"""#156 Track A: judge a completed job's subtitle with subarr's own judges.

Pure wrapper over `tournament.score_entrant` (readability #92 + failure-mode
signals). NO accuracy/QE here (that's L3/#123) and NO VAD, so silence/uncovered
signals are inert — the trustworthy detectors are readability, looping (repeats)
and canned-hallucination. The composite is a failure-absence + readability
rollup, NOT a quality grade; callers must present it as such.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .tournament import Entrant, score_entrant

# Flag-bar thresholds (tunable; exposed as constants so Settings can surface
# them later without touching logic).
AFTERCARE_COMPOSITE_MIN = 65.0
AFTERCARE_REPEAT_MAX = 0.20


@dataclass
class AftercareEvaluation:
    composite: float
    cue_count: int
    flagged: bool
    readability: dict[str, Any] | None   # ReadabilityReport.to_dict() or None
    signals: dict[str, Any] | None       # score_entrant signals or None


def _is_flagged(card) -> bool:
    """A job is flagged when any Track-A-available signal trips. (silence_text /
    uncovered need VAD → inert here; qe_adequacy needs source → inert here.)"""
    if card.disqualified:
        return True
    sig = card.signals or {}
    if (sig.get("canned_phrase_hits") or 0) > 0:
        return True
    if (sig.get("repeated_line_ratio") or 0.0) > AFTERCARE_REPEAT_MAX:
        return True
    issues = (card.readability or {}).get("issues", [])
    if any(i.get("severity") == "critical" for i in issues):
        return True
    if (card.composite or 0.0) < AFTERCARE_COMPOSITE_MIN:
        return True
    return False


def evaluate_subtitle(srt_text: str) -> AftercareEvaluation:
    """Judge one produced subtitle. No source_text / speech_ranges (Track A)."""
    card = score_entrant(Entrant(label="aftercare", srt_text=srt_text))
    return AftercareEvaluation(
        composite=float(card.composite),
        cue_count=int(card.cue_count),
        flagged=_is_flagged(card),
        readability=card.readability,
        signals=card.signals,
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/test_aftercare.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/subarr/aftercare.py tests/test_aftercare.py
git commit -m "feat(#156): aftercare evaluation + flag bar (pure)"
```

---

## Task 3: `aftercare_store.py` — `AfterCareStore`

**Files:**
- Create: `src/subarr/aftercare_store.py`
- Test: `tests/test_aftercare_store.py` (append)

- [ ] **Step 1: Append failing store tests**

Append to `tests/test_aftercare_store.py`:

```python
def _store(tmp_path):
    from subarr.aftercare_store import AfterCareStore
    return AfterCareStore(_migrated_db(tmp_path))


def _ev(flagged, composite=40.0):
    from subarr.aftercare import AftercareEvaluation
    return AftercareEvaluation(
        composite=composite, cue_count=10, flagged=flagged,
        readability={"issues": []}, signals={"repeated_line_ratio": 0.0,
                                              "canned_phrase_hits": 0},
    )


def test_record_and_pending_count(tmp_path):
    s = _store(tmp_path)
    s.record(canonical_path="TV/A/e1.mkv", completed_at=1.0,
             evaluation=_ev(True), source="subgenscan")
    s.record(canonical_path="TV/A/e2.mkv", completed_at=1.0,
             evaluation=_ev(False, composite=95.0), source="subgenscan")
    assert s.pending_count() == 1                     # only the flagged one


def test_list_results_views_and_latest_per_path(tmp_path):
    s = _store(tmp_path)
    # same path twice — a requeue: first flagged, then clean & newer
    s.record(canonical_path="TV/A/e1.mkv", completed_at=1.0,
             evaluation=_ev(True), source="subgenscan")
    s.record(canonical_path="TV/A/e1.mkv", completed_at=2.0,
             evaluation=_ev(False, composite=95.0), source="manual")
    flagged = s.list_results(view="flagged", limit=50, offset=0)
    assert flagged == []                              # latest is clean → not pending
    all_rows = s.list_results(view="all", limit=50, offset=0)
    assert len(all_rows) == 1                          # latest-per-path
    assert all_rows[0]["composite"] == 95.0


def test_mark_reviewed(tmp_path):
    s = _store(tmp_path)
    s.record(canonical_path="TV/A/e1.mkv", completed_at=1.0,
             evaluation=_ev(True), source="subgenscan")
    row = s.list_results(view="flagged", limit=50, offset=0)[0]
    assert s.mark_reviewed(row["id"]) is True
    assert s.pending_count() == 0
    assert s.mark_reviewed(999999) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/test_aftercare_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'subarr.aftercare_store'`.

- [ ] **Step 3: Implement `aftercare_store.py`**

Create `src/subarr/aftercare_store.py`:

```python
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


class AfterCareStore:
    def __init__(self, db_path: Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(db_path), check_same_thread=False, isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()

    def record(self, *, canonical_path: str, completed_at: float,
               evaluation: AftercareEvaluation, source: str | None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO aftercare_results "
                "(canonical_path, completed_at, composite, cue_count, flagged, "
                " readability_json, signals_json, source, reviewed_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                (
                    canonical_path, completed_at, evaluation.composite,
                    evaluation.cue_count, 1 if evaluation.flagged else 0,
                    json.dumps(evaluation.readability) if evaluation.readability else None,
                    json.dumps(evaluation.signals) if evaluation.signals else None,
                    source, time.time(),
                ),
            )

    # latest-per-path: a row is "current" iff no newer row exists for its path.
    _LATEST = (
        "a.completed_at = (SELECT MAX(b.completed_at) FROM aftercare_results b "
        "WHERE b.canonical_path = a.canonical_path)"
    )

    def pending_count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                f"SELECT COUNT(*) FROM aftercare_results a "
                f"WHERE a.flagged = 1 AND a.reviewed_at IS NULL AND {self._LATEST}"
            ).fetchone()
        return int(row[0])

    def list_results(self, *, view: str, limit: int, offset: int) -> list[dict[str, Any]]:
        where = self._LATEST
        if view == "flagged":
            where += " AND a.flagged = 1 AND a.reviewed_at IS NULL"
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM aftercare_results a WHERE {where} "
                f"ORDER BY a.flagged DESC, a.completed_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get(self, result_id: int) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute(
                "SELECT * FROM aftercare_results WHERE id = ?", (result_id,),
            ).fetchone()
        return self._row_to_dict(r) if r else None

    def mark_reviewed(self, result_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE aftercare_results SET reviewed_at = ? "
                "WHERE id = ? AND reviewed_at IS NULL",
                (time.time(), result_id),
            )
            return cur.rowcount > 0

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
            "reviewed_at": r["reviewed_at"],
        }
```

- [ ] **Step 4: Run to verify it passes**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/test_aftercare_store.py -v`
Expected: PASS (all store tests).

- [ ] **Step 5: Commit**

```bash
git add src/subarr/aftercare_store.py tests/test_aftercare_store.py
git commit -m "feat(#156): AfterCareStore (record/pending/list/review)"
```

---

## Task 4: Wire `AfterCareStore` onto `app.state`

**Files:**
- Modify: `src/subarr/app.py` (lifespan — store construction + close)

First READ `src/subarr/app.py` around the lifespan store registrations (search for `app_.state.audio_lang =` / `app_.state.scans =` to find the block, and the `finally:` shutdown block).

- [ ] **Step 1: Construct the store in the lifespan**

In the lifespan, alongside the other store constructions (e.g. just after `app_.state.audio_lang = AudioLangStore(...)`), add:

```python
    from .aftercare_store import AfterCareStore
    app_.state.aftercare = AfterCareStore(settings.db_path)
```

(Use the same `db_path` expression the sibling stores use — match `AudioLangStore`'s argument exactly, e.g. `settings.db_path` or `Path(settings.db_path)`.)

- [ ] **Step 2: Close it on shutdown**

In the lifespan `finally:` block, alongside the other store closes, add:

```python
    try:
        app_.state.aftercare._conn.close()
    except Exception:
        pass
```

(Match how sibling stores are closed if they expose a `close()` — if `AudioLangStore` is closed via `._conn.close()`, mirror that; otherwise add a `close()` method to `AfterCareStore` and call it.)

- [ ] **Step 3: Verify the app still boots**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -c "import subarr.app"`
Expected: no import error.

- [ ] **Step 4: Commit**

```bash
git add src/subarr/app.py
git commit -m "feat(#156): construct AfterCareStore on app.state"
```

---

## Task 5: Completion hook — judge on complete

**Files:**
- Modify: `src/subarr/completion_watcher.py`
- Modify: `src/subarr/app.py` (pass the store to `CompletionWatcher`)
- Test: `tests/test_aftercare_completion.py`

First READ `completion_watcher.py`: the `CompletionWatcher.__init__` signature, `complete_entry(self, entry)` (the `self._provenance.mark_completed(entry.id)` line), and `_find_srt_sidecar`. Confirm `log` and `import time` exist at module level (add if missing).

- [ ] **Step 1: Write the failing hook test**

Create `tests/test_aftercare_completion.py`:

```python
"""#156: aftercare judging fires (best-effort) on job completion."""
from __future__ import annotations

import pytest

from subarr.aftercare_store import AfterCareStore
from subarr.completion_watcher import CompletionWatcher


def _store(tmp_path):
    from subarr.migrate import run_migrations
    db = tmp_path / "a.db"
    run_migrations(db)
    return AfterCareStore(db)


class _Entry:
    id = 1
    canonical_path = "TV/Show/S01E01.mkv"
    source = "subgenscan"


def _watcher(store, **kw):
    # Construct with only what _run_aftercare needs; other deps unused here.
    w = CompletionWatcher.__new__(CompletionWatcher)
    w._aftercare = store
    return w


def test_run_aftercare_records_result(tmp_path, monkeypatch):
    store = _store(tmp_path)
    srt = tmp_path / "S01E01.en.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nHello there.\n\n",
                   encoding="utf-8")
    w = _watcher(store)
    monkeypatch.setattr(w, "_find_srt_sidecar", lambda p: str(srt))
    w._run_aftercare(_Entry())
    rows = store.list_results(view="all", limit=10, offset=0)
    assert len(rows) == 1
    assert rows[0]["canonical_path"] == "TV/Show/S01E01.mkv"


def test_run_aftercare_no_srt_is_noop(tmp_path):
    store = _store(tmp_path)
    w = _watcher(store)
    w._find_srt_sidecar = lambda p: None
    w._run_aftercare(_Entry())                 # must not raise
    assert store.list_results(view="all", limit=10, offset=0) == []


def test_run_aftercare_never_raises_on_bad_input(tmp_path, monkeypatch):
    store = _store(tmp_path)
    w = _watcher(store)
    monkeypatch.setattr(w, "_find_srt_sidecar",
                        lambda p: (_ for _ in ()).throw(OSError("boom")))
    w._run_aftercare(_Entry())                 # best-effort: swallows the error
    assert store.list_results(view="all", limit=10, offset=0) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/test_aftercare_completion.py -v`
Expected: FAIL (`_run_aftercare` / `_aftercare` not defined).

- [ ] **Step 3: Add the hook to `CompletionWatcher`**

In `completion_watcher.py`, add to `__init__` a new optional param `aftercare_store=None` and store it: `self._aftercare = aftercare_store`. Ensure these imports exist at the top:

```python
import time
from pathlib import Path
from .aftercare import evaluate_subtitle
```

Add this method to the class:

```python
    def _run_aftercare(self, entry) -> None:
        """#156: judge the produced subtitle and record the result. Best-effort
        — a failure here must NEVER block completion / the loop."""
        if not getattr(self, "_aftercare", None):
            return
        try:
            srt_path = self._find_srt_sidecar(entry.canonical_path)
            if not srt_path:
                return
            text = Path(srt_path).read_text(encoding="utf-8", errors="replace")
            ev = evaluate_subtitle(text)
            self._aftercare.record(
                canonical_path=entry.canonical_path,
                completed_at=time.time(),
                evaluation=ev,
                source=getattr(entry, "source", None) or "subgenscan",
            )
        except Exception as e:  # noqa: BLE001 — aftercare must never break completion
            log.warning("aftercare judging failed for %s: %s",
                        getattr(entry, "canonical_path", "?"), e)
```

In `complete_entry`, immediately AFTER `self._provenance.mark_completed(entry.id)`, add:

```python
        self._run_aftercare(entry)
```

- [ ] **Step 4: Pass the store from app.py**

In `app.py` where `CompletionWatcher(...)` is constructed, add the kwarg `aftercare_store=app_.state.aftercare`. (READ the construction site first; add it to the existing call.)

- [ ] **Step 5: Run to verify it passes**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/test_aftercare_completion.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/subarr/completion_watcher.py src/subarr/app.py tests/test_aftercare_completion.py
git commit -m "feat(#156): judge subtitles on job completion (best-effort)"
```

---

## Task 6: Router — pending / results / acknowledge

**Files:**
- Create: `src/subarr/routers/aftercare.py`
- Modify: `src/subarr/app.py` (include router + register `/aftercare` screen)
- Test: `tests/test_aftercare_router.py`

First READ how another router (e.g. `routers/audio_lang.py`) is included in `app.py` (search `include_router`) and how screens are registered (search `_V1_SCREENS`).

- [ ] **Step 1: Implement the router**

Create `src/subarr/routers/aftercare.py`:

```python
"""#156 Track A: aftercare review endpoints.

GET  /api/aftercare/pending             — flagged & unreviewed count (header pill)
GET  /api/aftercare/results?view=&...   — latest-per-path results (page)
POST /api/aftercare/{id}/acknowledge    — mark reviewed

Requeue is NOT here — the frontend reuses POST /api/queue/requeue (which already
resolves audio_language_override) then calls acknowledge. DRY.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(prefix="/api/aftercare", tags=["aftercare"])


@router.get("/pending")
async def pending(request: Request) -> dict[str, Any]:
    store = request.app.state.aftercare
    return {"count": store.pending_count()}


@router.get("/results")
async def results(
    request: Request,
    view: str = Query("flagged", pattern="^(flagged|all)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    store = request.app.state.aftercare
    items = store.list_results(view=view, limit=limit, offset=offset)
    return {"count": len(items), "view": view, "items": items}


@router.post("/{result_id}/acknowledge")
async def acknowledge(result_id: int, request: Request) -> dict[str, Any]:
    store = request.app.state.aftercare
    if not store.mark_reviewed(result_id):
        raise HTTPException(404, detail=f"no pending aftercare result {result_id}")
    return {"ok": True, "id": result_id, "reviewed": True}
```

- [ ] **Step 2: Include the router + register the screen in app.py**

Where other routers are included, add:

```python
    from .routers import aftercare as aftercare_router
    app.include_router(aftercare_router.router)
```

In the `_V1_SCREENS` registration (mirror how `health` / `review` screens are registered), add an `aftercare` entry mapping the `/aftercare` route to `aftercare.html`. (READ the existing entries and copy the exact shape — e.g. `"aftercare": "aftercare.html"` or the tuple/list form used.)

- [ ] **Step 3: Write the router test**

Create `tests/test_aftercare_router.py`:

```python
"""#156 aftercare router."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBARR_DB_PATH", str(tmp_path / "a.db"))
    # Build a minimal app with the router + a real store.
    from fastapi import FastAPI
    from subarr.migrate import run_migrations
    from subarr.aftercare_store import AfterCareStore
    from subarr.aftercare import AftercareEvaluation
    from subarr.routers import aftercare as r
    db = tmp_path / "a.db"
    run_migrations(db)
    store = AfterCareStore(db)
    store.record(canonical_path="TV/A/e1.mkv", completed_at=1.0,
                 evaluation=AftercareEvaluation(40.0, 10, True, {"issues": []},
                                                {"canned_phrase_hits": 2}),
                 source="subgenscan")
    app = FastAPI()
    app.state.aftercare = store
    app.include_router(r.router)
    return TestClient(app)


def test_pending(client):
    assert client.get("/api/aftercare/pending").json() == {"count": 1}


def test_results_flagged(client):
    body = client.get("/api/aftercare/results?view=flagged").json()
    assert body["count"] == 1
    assert body["items"][0]["canonical_path"] == "TV/A/e1.mkv"
    assert body["items"][0]["flagged"] is True


def test_acknowledge(client):
    rid = client.get("/api/aftercare/results?view=flagged").json()["items"][0]["id"]
    assert client.post(f"/api/aftercare/{rid}/acknowledge").json()["ok"] is True
    assert client.get("/api/aftercare/pending").json() == {"count": 0}
    assert client.post(f"/api/aftercare/{rid}/acknowledge").status_code == 404
```

- [ ] **Step 4: Run to verify it passes**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/test_aftercare_router.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/subarr/routers/aftercare.py src/subarr/app.py tests/test_aftercare_router.py
git commit -m "feat(#156): aftercare router + /aftercare screen"
```

---

## Task 7: Frontend — the `/aftercare` page

**Files:**
- Create: `src/subarr/static/v1/home-hifi/aftercare.jsx`
- Create: `src/subarr/static/v1/home-hifi/entries/aftercare.entry.jsx`
- Create: `src/subarr/static/v1/home-hifi/aftercare.html`
- Test: visual + build (no unit test for JSX)

First READ `health.jsx`, `health.entry.jsx`, `health.html` (siblings) and `review.jsx` (for row/flag-chip/action patterns + the `AudioReviewModal` reuse). Match the hi-fi tokens (`--bg-*`, `--fg-*`, `--violet-500`, `--error-500`, `--text-*`) and `StatusDot`/`chip` atoms.

- [ ] **Step 1: Create the entry + html mirroring health's**

`entries/aftercare.entry.jsx` — copy `health.entry.jsx` exactly, replacing the imported page component with `AftercarePage` from `../aftercare.jsx` and mounting it the same way.

`aftercare.html` — copy `health.html` exactly, replacing the bundle script reference `health.bundle.js` → `aftercare.bundle.js` and the title text.

- [ ] **Step 2: Create `aftercare.jsx`**

Create `src/subarr/static/v1/home-hifi/aftercare.jsx`. It must:
- Poll `GET /api/aftercare/results?view=<flagged|all>` every ~8s (mirror health.jsx's poll), plus a `flagged | all` toggle that refetches.
- Render one row per result. **Lead with status + flags, never a positive grade.** Flagged rows: a red/amber severity dot (`--error-500` for composite<50 or any critical/canned; amber otherwise) + flag chips derived from `item.signals` + `item.readability.counts` (`Math.round(signals.repeated_line_ratio*100)+'% repeats'` when >0; `signals.canned_phrase_hits+' canned'` when >0; `readability.counts.cps+' CPS'` / `readability.counts.overlap+' overlap'` when present). Clean rows (in "all" view): a muted green "no problems detected".
- Per-row actions: **Acknowledge** (`POST /api/aftercare/{id}/acknowledge` → refetch), **Requeue** (`POST /api/queue/requeue` `{path: item.canonical_path}` THEN `POST /api/aftercare/{id}/acknowledge` → refetch), **🎧** (dispatch `open-audio-review` with `{title, _canonical_path: item.canonical_path}` and render `<AudioReviewModal/>` like review.jsx), and expand ▾.
- Expanded: list the offending cues from `item.readability.issues` (`#${i.cue} ${i.kind}/${i.severity}: ${i.detail}`) + a footer line `composite ${composite} · ${cue_count} cues · ${source}` with the composite shown muted and labelled "structural (not accuracy)".

Use this skeleton (fill the row/expand bodies following `review.jsx` styling):

```jsx
// #156 Track A: aftercare review page. Mirrors health.jsx (poll + row list +
// expand). Leads with failure flags; never presents a confident positive grade
// (accuracy score is L3/#123). Clean jobs read "no problems detected".
import { StatusDot } from './atoms.jsx';
import { AudioReviewModal } from './coverage.jsx';

const { useState, useEffect, useCallback } = React;

function badgeKind(item) {
  if (!item.flagged) return 'ok';
  const crit = (item.readability?.issues || []).some(i => i.severity === 'critical');
  if (item.composite < 50 || crit || (item.signals?.canned_phrase_hits || 0) > 0) return 'error';
  return 'warn';
}

function flagChips(item) {
  const out = [];
  const s = item.signals || {}, c = (item.readability || {}).counts || {};
  if ((s.repeated_line_ratio || 0) > 0) out.push(`${Math.round(s.repeated_line_ratio * 100)}% repeats`);
  if ((s.canned_phrase_hits || 0) > 0) out.push(`${s.canned_phrase_hits} canned`);
  if (c.cps) out.push(`${c.cps} CPS`);
  if (c.overlap) out.push(`${c.overlap} overlap`);
  return out;
}

export function AftercarePage() {
  const [view, setView] = useState('flagged');
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(null);

  const refetch = useCallback(async () => {
    const r = await fetch(`/api/aftercare/results?view=${view}`, { credentials: 'same-origin' });
    if (r.ok) setData(await r.json());
  }, [view]);

  useEffect(() => {
    refetch();
    const id = setInterval(refetch, 8000);
    return () => clearInterval(id);
  }, [refetch]);

  const acknowledge = useCallback(async (item) => {
    setBusy(item.id);
    try { await fetch(`/api/aftercare/${item.id}/acknowledge`, { method: 'POST', credentials: 'same-origin' }); }
    finally { setBusy(null); refetch(); }
  }, [refetch]);

  const requeue = useCallback(async (item) => {
    setBusy(item.id);
    try {
      await fetch('/api/queue/requeue', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin', body: JSON.stringify({ path: item.canonical_path }),
      });
      await fetch(`/api/aftercare/${item.id}/acknowledge`, { method: 'POST', credentials: 'same-origin' });
    } finally { setBusy(null); refetch(); }
  }, [refetch]);

  const listen = useCallback((item) => {
    window.dispatchEvent(new CustomEvent('open-audio-review', {
      detail: { title: item.canonical_path.split('/').slice(-1)[0],
                _canonical_path: item.canonical_path },
    }));
  }, []);

  // … render: header with `flagged | all` toggle (setView), then data.items.map
  //   to a row using badgeKind(item), flagChips(item), the three action buttons
  //   (disabled when busy===item.id), and an expandable detail block listing
  //   item.readability.issues + the muted "structural (not accuracy)" footer.
  //   Empty state when !data.items.length. Follow review.jsx's row markup +
  //   hi-fi tokens. Always render <AudioReviewModal/> at the end.
}
```

- [ ] **Step 3: Build the frontend**

Run: `npm run build:frontend`
Expected: output lists an `aftercare` bundle (`built bundles for: …, aftercare`). If it doesn't, the build script discovers entries from `entries/*.entry.jsx` — confirm the entry filename matches the pattern.

- [ ] **Step 4: Manual verify**

Restart the dev container (`docker restart subarr-next`), open `http://localhost:9923/aftercare`, confirm the page renders, the toggle works, and (if any flagged rows exist) the chips + actions appear. If no data, confirm the empty state renders.

- [ ] **Step 5: Commit**

```bash
git add src/subarr/static/v1/home-hifi/aftercare.jsx \
        src/subarr/static/v1/home-hifi/entries/aftercare.entry.jsx \
        src/subarr/static/v1/home-hifi/aftercare.html \
        src/subarr/static/v1/home-hifi/aftercare.bundle.js \
        src/subarr/static/v1/home-hifi/aftercare.bundle.js.map
git commit -m "feat(#156): aftercare review page"
```

---

## Task 8: Frontend — header pill + Operations rail item

**Files:**
- Modify: `src/subarr/static/v1/home-hifi/chrome.jsx`

First READ `chrome.jsx`: `_fetchChromeCounts` (the `Promise.all` of endpoint fetches, ~line 42), the health pill markup in `TopBar` (~line 260), and `railItems()` (~line 154).

- [ ] **Step 1: Fetch the pending count**

In `_fetchChromeCounts`, add `fetch('/api/aftercare/pending', { credentials: 'same-origin' }).then(r => r.ok ? r.json() : null).catch(() => null)` to the `Promise.all`, and after the results destructure set `next.aftercare_count = aftercareData?.count || 0;` (name the destructured var to match the others' style).

- [ ] **Step 2: Add the header pill**

In `TopBar`, mirroring the health pill block exactly, add a pill that renders when `counts.aftercare_count > 0`, labelled `Aftercare · {counts.aftercare_count}`, linking to `/aftercare`. Use the same chip/StatusDot styling as the health pill (warn/violet tone, not error).

- [ ] **Step 3: Add the Operations rail item**

In `railItems()` under the `operations` group, add `{ id: 'aftercare', label: 'Aftercare', href: '/aftercare', count: counts.aftercare_count }` following the existing item shape (match how `review`/`queue` items pass their counts).

- [ ] **Step 4: Build + commit**

```bash
npm run build:frontend
git add src/subarr/static/v1/home-hifi/chrome.jsx \
        src/subarr/static/v1/home-hifi/chrome.bundle.js \
        src/subarr/static/v1/home-hifi/chrome.bundle.js.map
git commit -m "feat(#156): aftercare header pill + rail item"
```

(Note: `chrome.jsx` is inlined into every page bundle. `npm run build:frontend` rebuilds all bundles; commit only the bundles whose `.bundle.js` actually changed — `git status` then add those. Revert spurious `.map`-only drift.)

---

## Task 9: Frontend — dashboard panel

**Files:**
- Modify: `src/subarr/static/v1/home-hifi/dashboard.jsx`

First READ `dashboard.jsx`: the panel layout between the stage tiles and the activity feed, and `useLiveChromeCounts`/`useLiveDashboard` (whichever already exposes counts).

- [ ] **Step 1: Add the panel**

Add an `AfterCarePanel` component that reads the aftercare count (reuse `useLiveChromeCounts()` if dashboard already imports it; else poll `/api/aftercare/pending`) and renders ONLY when `count > 0`: a compact panel "N job(s) need review →" linking to `/aftercare`, styled with hi-fi tokens to match the other dashboard panels. Place it between the stage tiles and the activity feed.

- [ ] **Step 2: Build + commit**

```bash
npm run build:frontend
git add src/subarr/static/v1/home-hifi/dashboard.jsx \
        src/subarr/static/v1/home-hifi/dashboard.bundle.js \
        src/subarr/static/v1/home-hifi/dashboard.bundle.js.map
git commit -m "feat(#156): aftercare dashboard panel"
```

---

## Task 10: Full verification

- [ ] **Step 1: Run the whole backend suite**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/ --ignore=tests/e2e -q`
Expected: all pass (the prior 743 + the new aftercare tests). Windows asyncio-cleanup ResourceWarnings are pre-existing noise.

- [ ] **Step 2: Confirm bundles are current**

Run: `npm run build:frontend` then `git status` — there should be no uncommitted `.bundle.js` changes (only possibly `.map` drift, which is ignored by the drift gate; revert it).

- [ ] **Step 3: End-to-end smoke (dev container)**

`docker restart subarr-next`, then trigger/await a real completion (or insert a row via the store) and confirm: the header pill appears, `/aftercare` lists it, Acknowledge clears it, Requeue resubmits + clears.

- [ ] **Step 4: Open the PR**

```bash
git push -u origin feat/156-job-aftercare
gh pr create --repo coaxk/subarr --base main --head feat/156-job-aftercare \
  --title "feat(#156): job aftercare (Track A) — quality review on completed jobs" \
  --body "Implements Track A of #156 (does not close it — L3/L4 remain). See docs/superpowers/specs/2026-06-08-job-aftercare-design.md. Follow-ups: #165, #123, #64/#67/#68, #95."
```

Expected: CI green (incl. bundle-drift gate). Do not merge without review.

---

## Notes / guardrails recap

- The composite is **failure-absence + readability, not accuracy** — UI never sells a positive grade; clean = "no problems detected". Positive accuracy score is L3 (#123).
- Judging is **best-effort + synchronous** in `complete_entry` — must never raise into the completion path.
- `silence_text` / `uncovered_speech` are inert (no VAD) and intentionally NOT in the flag bar — deferred (needs a VAD pass, #111).
- Migration **017** (016 reserved by the open #159 PR).
