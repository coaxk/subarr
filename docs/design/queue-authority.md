# Design: subarr as queue authority (#66 queue mutation + #116 throttled backfill)

**Status:** proposed — awaiting sign-off before build
**Issues:** #66 (promote/demote/reorder/pause), #116 (throttled library-backfill)
**Author:** Edmund · 2026-06-07

## Why
Today subarr is **fire-and-forget**: `scan_runner` calls subgen `/batch <directory>`,
subgen walks the dir and **owns the queue**, and subarr can only *cancel*
(`queue_cancel`, the only mutation subgen v4.13 exposes). So:
- **#66** (reorder/pause/promote/demote) is impossible — there's no subarr-side
  backlog to reorder and no subgen endpoint to reorder *its* queue.
- **#116** (hold the queue at a target depth) is impossible — we flood subgen.

Rather than add mutation endpoints to the subgen fork (separate repo, new
capability round-trip, still can't reorder *foreign* items), **subarr becomes
the queue authority for the jobs it submits**, feeding subgen at a controlled
depth. Reorder/pause/promote/demote all become subarr-side and instant; #116
falls out of the same mechanism for free.

## Core model
A new **subarr-owned pending queue** (persisted) sits *in front of* subgen.

```
[Gaps / Coverage / Backfill / Auto-queue / Manual]
        │  enqueue (priority, override, task)
        ▼
   pending_queue (subarr-owned, SQLite, reorderable, pausable)
        │  feeder loop: while not paused and effective_subgen_depth < target_depth:
        │                 pop highest-priority pending → submit ONE to subgen
        ▼
   subgen /queue  ── SHARED ── also fed by other producers (Bazarr/Plex hooks,
        │                       direct subgen scans, other tools)
        ▼
   completion_watcher (already exists) reconciles completion by canonical_path
```

- **Pending items** (not yet handed to subgen) = fully subarr-controlled →
  reorder / promote / demote / pause / remove, instant, no subgen dependency.
- **Submitted items** (already in subgen) = **cancel-only**, exactly as today.
  We never pretend to reorder what subgen already holds.
- The UI makes this boundary explicit so the affordances aren't misleading.

## The shared-queue problem (subgen is NOT ours alone)
subgen's queue can contain items subarr never submitted (direct subgen scans,
Bazarr/Plex webhooks, another tool against the same subgen). The design treats
subgen's queue as **shared, observed-not-owned**:

1. **Depth is measured on the TOTAL.** The feeder targets
   `effective_depth = len(queued) + len(processing)` from subgen `/queue`
   (foreign items included). If subgen is busy with someone else's work, subarr
   **backs off** and doesn't pile on. We co-exist, we don't fight.
2. **We only ever act on OUR items.** subarr correlates subgen-queue paths
   against its *submitted set* (canonical_path ↔ provenance ledger /
   pending_queue rows). Foreign items are shown **read-only** ("external —
   queued on subgen") and never cancelled/reordered by subarr.
3. **Pre-submit dedup.** Before feeding a path, the feeder checks it isn't
   already in subgen `/queue` (a foreign producer may have submitted the same
   file) → skip/merge instead of double-queueing. (Extends today's dedup.)
4. **Reconciliation each tick** (reuses `completion_watcher` logic): a submitted
   path that's gone from subgen's queued+processing → completed; still present →
   in flight; present-but-not-ours → external.

This is the consideration Judd raised: *"subgen may add other items in
before/as we fire into it."* Handled by (1) targeting total depth and (2)
ownership-correlation — never assume the slot we see is the slot we filled.

## Granularity: per-item submission via `/batch` single-file (RESOLVED)
To control order + depth we submit **one job at a time**, not a whole-directory
`/batch` walk (which queues many at once and hands ordering to subgen).

**Channel decision (resolved 2026-06-07):** submit each job as **`/batch` with a
single FILE path**. The subgen-surface audit confirms `/batch`'s `directory`
param accepts "a folder **/file** on disk" (`docs/research/subgen-surface.md`),
and subarr already queues movies as single files this way. This is the only
channel that satisfies all three requirements:

| channel | writes .srt to disk | shows in subgen `/queue` (→ our GUI) | per-file |
|---------|:--:|:--:|:--:|
| `/batch` single-file | ✅ | ✅ (native, async) | ✅ |
| `/asr` path-input | ❌ (returns text, no disk write) | ❌ (sync, never queued) | ✅ |

`/asr` is REJECTED for the queue path: it's the arena's *measurement* channel —
synchronous, returns the subtitle text over HTTP, writes nothing to disk, and
never appears in subgen's `/queue`, so it would be invisible in the queue GUI
(Judd's requirement) and wouldn't actually produce a usable sidecar.

Because `/batch` single-file is **async + queue-visible**, the feeder stays
fire-and-poll (no blocking), and the existing queue GUI + `completion_watcher`
reconciliation work unchanged — submitted jobs appear in subgen `/queue` and are
matched back by canonical_path exactly as today.

- **Verify at build (slice 2):** confirm single-file `/batch` against an
  already-subbed file (subgen skips → `walked=1, skipped=1, queued=0`, zero GPU)
  to prove file-path handling + `/queue` visibility empirically before wiring.

## #116 falls out for free
`target_depth` IS the throttle. Backfill = a producer that bulk-adds gaps to
`pending_queue` at **low priority**; the feeder drains them keeping subgen at
`target_depth`, never flooding, yielding to higher-priority + foreign work.
One mechanism serves both #66 and #116.

## Data model
New table (migration 014):
```sql
CREATE TABLE IF NOT EXISTS pending_queue (
  id             TEXT PRIMARY KEY,          -- uuid
  canonical_path TEXT NOT NULL,
  position       REAL NOT NULL,             -- sort key; gaps allow O(1) reorder
  priority       INTEGER NOT NULL DEFAULT 0,-- bucket: manual>gaps>backfill
  status         TEXT NOT NULL DEFAULT 'pending', -- pending|paused|submitted|done|error
  audio_language_override TEXT,
  task           TEXT,                      -- transcribe|translate|null
  source         TEXT NOT NULL,             -- manual|gaps|backfill|auto
  submitted_at   REAL, created_at REAL NOT NULL, error TEXT
);
CREATE INDEX IF NOT EXISTS idx_pending_queue_status_pos ON pending_queue(status, position);
```
- `position` as REAL → reorder = set position to midpoint of neighbors (no mass
  renumber). Promote = min-ε; demote = max+ε.
- Survives restart; feeder resumes from `status='pending'`.

## Feeder loop
- Supervised via #157 `task_health` ("queue-feeder") — it's a long-running loop,
  so it MUST report success/failure (the #79 lesson).
- Tick (e.g. every 5–10s, or event-driven on enqueue/completion):
  1. if `paused` → no-op.
  2. read subgen `/queue` → `effective_depth`, `subgen_paths`.
  3. while `effective_depth < target_depth` and pending non-empty:
     pop highest-priority/lowest-position pending; if its path already in
     `subgen_paths` → mark submitted (dedup); else submit via `/asr` path-input;
     mark `submitted`; `effective_depth += 1`.
- `target_depth` default **2** (configurable in AutoQueueRules): small enough to
  keep reorder meaningful, large enough to keep the GPU fed. (Higher depth =
  more rushes into subgen = less reorderable; tradeoff documented.)

## API (new, prefix /api/queue)
- `GET  /api/queue/pending` — the reorderable pending list.
- `POST /api/queue/pending/{id}/move` `{to_position|before_id|after_id}` — reorder.
- `POST /api/queue/pending/{id}/promote` · `/demote` — bucket bumps.
- `POST /api/queue/pause` · `/resume` — global feed pause (status flips,
  in-flight subgen items keep running — honest).
- `DELETE /api/queue/pending/{id}` — drop a pending item.
- Existing `requeue` / `cancel` / `clear` stay; `cancel` still governs *submitted*
  items only.

## UI (queue.jsx)
Sections, ownership-explicit:
- **Pending (subarr)** — drag / ▲▼ reorder, promote/demote, remove. Pause toggle
  at the section header.
- **Submitted · Processing (subarr)** — cancel-only.
- **External (on subgen)** — read-only, labeled "queued by another source."
- **Recently done / Issues** — as today.

## Migration / compatibility
- Route the existing producers (coverage_actions.queue, scheduler auto-queue,
  manual) **through** the pending queue instead of straight to `scan_runner`.
  scan_runner becomes the feeder's submit primitive.
- Default `target_depth` high-ish on first deploy? No — default 2; existing
  behavior (flood) changes to throttled, which is the point. Document in CHANGELOG.
- Fresh table; nothing to backfill.

## Build slices (each tested + committed)
1. `pending_queue` store + migration 014 + job model + tests.
2. feeder loop (depth-aware, pause-aware, per-item submit via `/asr`) +
   #157 supervision + dedup/reconciliation + tests.
3. API (list/move/promote/demote/pause/resume/remove) + tests.
4. UI sections + reorder controls + pause + ownership labels.
5. Fold in #116: backfill producer (bulk low-priority enqueue) + `target_depth`
   knob in rules + "drain to depth" + tests.
6. Route auto-queue/scheduler/manual through the pending queue; deprecate the
   direct flood path.

## Decisions (signed off 2026-06-07)
- **target_depth default = 2** (behavior change flood→throttle: approved).
- **Channel = `/batch` single-file** (native subgen `/queue` visibility + writes
  the sub). `/asr` rejected (invisible in GUI, no disk write). [was OPEN-1]
- **Per-source priority buckets** (manual > gaps > backfill) — user-tunable.

## Open questions (resolve at build)
- **OPEN-2:** feeder cadence — pure poll (simple) vs event-driven on
  enqueue/completion (snappier). Start poll@5s, revisit.
- **OPEN-4:** does routing auto-queue through pending change the probe-gate /
  in_flight_paths semantics auto_queue.evaluate relies on? (Likely fine — in
  flight now spans pending+submitted; confirm.)

## Non-goals
- Reordering items already inside subgen (impossible without subgen endpoints;
  cancel-only stays).
- Touching foreign items.
- Subgen-fork changes (approach A) — explicitly avoided.
