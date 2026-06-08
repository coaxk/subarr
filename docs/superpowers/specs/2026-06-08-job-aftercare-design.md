# Job Aftercare (Track A) — Design Spec

**Issue:** #156 (Track A) · **Date:** 2026-06-08 · **Status:** approved, pre-implementation
**Follow-ups:** #165 (Open in Tuning Lab), #123 (L3 QE score), #64/#67/#68 (L4 comparative), #95 (passive-tuning capture)

## Problem

The operations path (Gaps/Library → queue → subgen → done) assumes "file exists = success" and discards all quality signal. Whisper output is wildly variable (hallucination, looping, garbage on music, cue-over-silence, timing). The user gets a `.srt` with no idea if it's good or junk — nothing in the *arr space tells them. Aftercare closes that loop: judge every completed job, surface the bad ones for review, and silently record every result as the data foundation passive-tuning (#95) later feeds on.

This is **Track A** = L1 readability + L2 failure-flags + the three UX surfaces. L3 (positive QE score) and L4 (comparative) are explicitly deferred — see Follow-ups.

## Scoring: provenance & what it means (read before building)

**The score is 100% subarr's own judges, computed from the produced `.srt` content — Bazarr is never consulted.** Bazarr assigns a *flat* score to anything Whisper-generated (it has no provider-match to grade against), so it cannot tell a great sub from garbage. That blind spot is the entire reason aftercare exists.

**Critical honesty limits of the Track A composite** (verified in `tournament.score_entrant`):
- It measures **failure-mode absence + readability**, NOT translation accuracy. With no `source_text`, `qe_adequacy` is `None` and `composite = 100 − (repeat/canned/silence/coverage penalties) − capped readability penalty`. A fluent, well-timed subtitle of the **wrong language** still scores ~100. Semantic accuracy is the **positive-quality-gap** — math can't see meaning. The trustworthy positive 0–100 *accuracy* grade is **L3 (#123, LaBSE QE)** and is deliberately deferred so we never "cry wolf" with a confident grade we can't back.
- **`silence_text_ratio` and `uncovered_speech_ratio` require VAD speech-ranges**, which Track A does not compute (it would mean a VAD pass on the audio — heavier, breaks "instant / never-block"). They return 0 and are **inert** in Track A. So Track A's real, trustworthy detectors are: **readability (L1, CPS/CPL/overlap/duration) + looping (`repeated_line_ratio`) + canned-hallucination (`canned_phrase_hits`)**.

**Consequence for the UI:** lead with the high-precision **failure flags** we can trust. Clean jobs are presented as **"no problems detected"** (structural checks passed) — never a confident positive grade like "94/100 = great". The numeric composite is stored (for #95 + L3) and drives the flag threshold + sort, but is shown only as a muted, honestly-labelled "structural score (readability + failure checks — not accuracy)", if shown at all. The confident quality grade arrives with L3.

## Principles

- **Never block the job.** Judging is best-effort (`try/except`) inside the completion path; a judge failure must never stop the Bazarr write-back or the loop. (Mirrors the #79 silent-loop lesson: supervise, never let aftercare crash a background loop.)
- **Reuse, don't rebuild.** The judges (#92 readability + tournament signals) already exist; Track A is wiring + storage + surfacing, near-zero new judging logic.
- **Store everything, surface the problems.** Hybrid scope: persist every completed job's result (foundation for #95); default view + notification show flagged-only, with a "show all" toggle.
- **Suggest, don't auto-act.** Actions are user-initiated (Acknowledge / Requeue). No auto-requeue (loops/burns GPU).
- **Follow existing patterns.** Backend mirrors `ErrorStore`/`TaskHealthStore`; frontend mirrors `health.jsx`/`chrome.jsx`/`dashboard.jsx` and uses the hi-fi design tokens — no bespoke styling.

## Architecture & data flow

```
subgen completes a job
   │
CompletionWatcher.complete_entry(entry)          # completion_watcher.py
   ├─ provenance.mark_completed(entry.id)         # (unchanged)
   ├─ [NEW] aftercare judging (best-effort, sync, CPU-only, ms; .srt-only):
   │     srt_path = self._find_srt_sidecar(entry.canonical_path)   # already exists
   │     if srt_path:
   │         text  = Path(srt_path).read_text(encoding='utf-8', errors='replace')
   │         # No speech_ranges (VAD) and no source_text (QE) in Track A → the
   │         # active signals are readability + repeats + canned only.
   │         card  = tournament.score_entrant(Entrant(label=canonical, srt_text=text))
   │         flagged = _is_flagged(card)
   │         aftercare_store.record(canonical_path, completed_at, card, flagged, source)
   └─ Bazarr write-back / scan-disk / Plex partial-scan   # (unchanged, still runs)
```

Same hook serves the webhook-push path (`complete_by_canonical` → `complete_entry`). No new background loop — judging is synchronous (pure Python, milliseconds for a real SRT). VAD speech-ranges and QE/LaBSE are **not** used in Track A (`speech_ranges=None`, `source_text=None`), so `silence_text` contributes 0 and there is no GPU/model dependency.

## Flag bar (Balanced)

A job is `flagged` when **any** of the **Track-A-available** signals trip:
- `canned_phrase_hits > 0` (Whisper hallucination phrases), OR
- `repeated_line_ratio > 0.20` (looping), OR
- any **critical** readability issue (`cps > 25`, or `overlap`), OR
- `composite < 65` (structural rollup of the above — catches an accumulation of minor readability issues).

Note these are all derivable from the `.srt` alone (no VAD, no source). `silence_text` / `uncovered_speech` are **not** in the Track A bar — they're inert without VAD (deferred, see Follow-ups). Thresholds live as module-level constants (`AFTERCARE_COMPOSITE_MIN = 65`, `AFTERCARE_REPEAT_MAX = 0.20`) so they're tunable without touching logic. `_is_flagged(card) -> bool` is a pure function (unit-tested in isolation).

## Persistence

New `aftercare_store.py` (mirrors `error_store.py` / `task_health.py`: single `sqlite3` connection, WAL, `threading.Lock`, registered on `app.state.aftercare` in the lifespan, closed in `finally`).

Migration **`017_aftercare_results.sql`** (bump to 018 if #159's migration 016 merges first — confirm main's highest at build time):

```sql
CREATE TABLE IF NOT EXISTS aftercare_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_path  TEXT NOT NULL,
    completed_at    REAL NOT NULL,
    composite       REAL NOT NULL,
    cue_count       INTEGER NOT NULL,
    flagged         INTEGER NOT NULL,          -- 0/1
    readability_json TEXT,                     -- ReadabilityReport.to_dict()
    signals_json    TEXT,                      -- {repeat_ratio, canned_hits, silence_text}
    source          TEXT,                      -- 'gaps' | 'library' | 'manual' | ...
    reviewed_at     REAL,                      -- NULL = pending review
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_aftercare_pending ON aftercare_results (flagged, reviewed_at, completed_at);
```

Store methods: `record(canonical_path, completed_at, card, flagged, source)`, `pending_count() -> int` (flagged=1 AND reviewed_at IS NULL), `list_results(view, limit, offset)` (view in {flagged, all}), `get(id)`, `mark_reviewed(id)`. Re-recording the same path appends a new row (history preserved — a requeue produces a fresh result). `list_results` returns **one row per `canonical_path` — the most recent by `completed_at`** (a correlated subquery / `GROUP BY canonical_path HAVING MAX(completed_at)`), so the UI shows current state, not every historical attempt; `flagged` view additionally filters `flagged=1 AND reviewed_at IS NULL`.

## API — `routers/aftercare.py` (prefix `/api/aftercare`)

- `GET /pending` → `{count}` — flagged & unreviewed; cheap indexed count; polled by `chrome.jsx`.
- `GET /results?view=flagged|all&limit=&offset=` → `{count, items:[...]}` — paginated; each item = path, composite, cue_count, flagged, the readability/signal breakdown, source, completed_at, reviewed_at.
- `POST /{id}/acknowledge` → sets `reviewed_at`; returns `{ok}`.
- `POST /{id}/requeue` → sets `reviewed_at` + calls the existing queue requeue path for the file; returns `{ok, requeued}`.

Registered in `app.py` alongside the other routers.

## Frontend (existing patterns only)

- **`aftercare.jsx`** + `entries/aftercare.entry.jsx` + `aftercare.html`; `/aftercare` route in `_V1_SCREENS`. Mirrors `health.jsx`: polls `GET /api/aftercare/results` (~8–10s), renders a row per result, expand-on-click for detail. Uses hi-fi tokens (`--bg-*`, `--fg-*`, `--violet-500`, `--error-500`), `StatusDot`/`chip` atoms.
  - Row leads with **status + flags, not a positive grade**. Flagged rows: a red/amber severity dot + the specific flag chips (`N% repeats`, `N canned`, `N CPS criticals`). Clean rows (in the "all" view): a muted green **"no problems detected"** — NOT a confident "94/100". Filename, actions Acknowledge / Requeue / 🎧 (reuses `AudioReviewModal`), expand ▾.
  - The numeric composite, if shown at all, is muted + labelled "structural score (readability + failure checks — not accuracy)" with a tooltip noting the accuracy grade arrives with L3 (#123). Never the headline.
  - Expanded: the offending cues (canned/repeat/CPS/overlap lines with timestamps) + footer (cue_count, completed-ago, source). Flag chips are the trustworthy signal; the composite is secondary.
  - `flagged | all` toggle (the hybrid view).
- **Header pill** (`chrome.jsx`): add `/api/aftercare/pending` to the `_fetchChromeCounts` `Promise.all`; set `next.aftercare_count`; render a pill in `TopBar` mirroring the Health pill (links to `/aftercare`); add an Operations sub-rail item with the count.
- **Dashboard panel** (`dashboard.jsx`): small `AfterCarePanel` between the stage tiles and activity feed, renders only when `aftercare_count > 0` ("N jobs need review →" → `/aftercare`).
- Build via `npm run build:frontend`; commit only the changed bundles (drift gate checks `.bundle.js`/`.html`; revert spurious `.map` drift).

## Testing (TDD)

- `_is_flagged` flag-bar logic across each trigger + the clean case (pure).
- The completion-hook judging: a fake completed entry + a temp `.srt` → asserts a result row recorded with correct composite/flags; best-effort (a malformed/missing srt records nothing and does not raise). Mirror `test_tournament.py` / `test_completion_watcher.py` if present.
- `AfterCareStore`: record → pending_count → list (flagged vs all) → mark_reviewed roundtrip (migrated temp DB, mirror `test_subtitle_readability.py` style).
- Migration 017 applies on a fresh DB (`test_migrations.py`).
- Router endpoints (pending/results/acknowledge/requeue) via the app test client.
- Full suite stays green; run `PYTHONPATH=src pytest tests/ --ignore=tests/e2e`.

## Out of scope / follow-ups (tracked)

| Deferred | Tracked by |
|---|---|
| "Open in Tuning Lab" row action (find a better config) | **#165** |
| **Positive accuracy grade (the confident 0–100 quality score)** | **#123** (LaBSE QE / summit) — Track A shows flags + "no problems detected", not a grade |
| L4 — comparative ("community-best for Korean") | **#64 / #67 / #68** (federated) |
| Clean-job data → federated/passive tuning | **#95** (aftercare is its user-facing surface) |
| **Silence-text + uncovered-speech flags (need a VAD pass on the audio)** | builds on silero VAD (**#111**); inert in Track A (`speech_ranges=None`). Fast-follow — run VAD async/throttled at completion, then these L2 flags light up. |

The store records **every** result (composite + signals + readability) from day one, so L3/L4 layer on without reshaping the schema.

## Delivery

Feature branch `feat/156-job-aftercare` → PR referencing (not auto-closing) #156 ("implements Track A"). #156 stays open as the umbrella for L3/L4. Not merged without review + green CI. After merge, a subarr release ships it (no new system deps — pure Python judges + SQLite).
