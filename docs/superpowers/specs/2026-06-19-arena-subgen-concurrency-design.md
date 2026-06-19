# Arena ↔ subgen concurrency coordination — design

**Date:** 2026-06-19
**Status:** Approved (design), pending implementation plan
**Repos touched:** `coaxk/subarr` (bulk) + `coaxk/subarr-subgen` (one-line capability)

## Problem

subarr drives subgen through two independent channels, and they don't coordinate:

1. **The normal queue** — subarr's `pending_queue` (a holding pen *inside subarr*) is drained by the feeder into subgen's `/batch`. subgen's worker pool runs these, governed by subgen's `CONCURRENT_TRANSCRIPTIONS` env (default 2). Plex/Bazarr webhooks fired directly at subgen also go through this pool.
2. **The Tuning-Lab arena** — fires subgen's `/asr` endpoint directly. `/asr` runs as a subgen "direct task" (`active_direct_tasks`) **outside** the worker pool, and there is **no shared inference lock** around `model.transcribe`.

Consequences, confirmed by reading subgen source (`subgen.py`):
- subgen's `GET /queue` reports only `task_queue` state (`queued`, `processing`, `idle`). It does **not** report `active_direct_tasks`. So an arena `/asr` transcription pegging the GPU shows `idle: true`.
- The arena therefore (a) does **not** respect `CONCURRENT_TRANSCRIPTIONS`, (b) is invisible to subarr's feeder back-off, and (c) is invisible in subarr's Queue UI (it only appears on the Tuning Lab surface).

Net effect: a user can have subgen set to "single job at a time" yet have an arena sweep running concurrently with a queue job. And a user watching the Queue page can fire batch jobs into GPU contention they can't see. The arena is the **only** producer of subgen load that subarr neither limits nor surfaces — and it's our own feature.

## Goal

Make subarr respect — and visibly reflect — the user's *actual* subgen concurrency, for every producer including the arena, without re-implementing or guessing that limit. It must behave correctly at both extremes: `CONCURRENT_TRANSCRIPTIONS=1` (strict serialize) and `=1000` (no artificial throttle).

## Non-goals (out of scope for this spec)

- **Hard, race-free enforcement inside subgen.** subgen's pool grabs queued jobs autonomously and foreign producers exist, so an *external* gate is necessarily soft (poll-based). A hard guarantee would require subgen's `/asr` to share the worker pool's semaphore — noted as a belt-and-suspenders follow-up, not built here. For subarr's use (subarr is the only `/asr` caller), the soft gate is correct in practice.
- **The `/batch` `audio_language_override` 3-letter normalization.** `resolve_audio_language_override()` (`audio_lang_store.py`) returns a raw verified `lang_code` that can be 3-letter ISO 639-2/B. Lower severity than the arena `/asr` bug (subgen `/batch` parses leniently / calls `.to_iso_639_1()`), but worth a one-line defensive `normalize_lang`. **Separate small follow-up — do not bundle here.**

## The core invariant

One condition governs all GPU-bound work subarr drives:

> **`subgen.processing_count + arena_in_flight < N`**, where `N` = subgen's `concurrent_transcriptions`.

- `processing_count` comes from subgen's existing `/queue` response and already includes foreign (Plex/Bazarr) jobs — so the gate covers every producer, not just subarr's.
- `arena_in_flight` is subarr's own count of running sweeps' active `/asr` calls. The arena's sweep semaphore is `max_concurrent=1` and within a sweep `/asr` calls are serial, so this is `0` or `1`.
- `N` is read live from subgen (see Piece 1). It is **not** a subarr-local constant — the budget mirrors the user's real setting.

Both producers consult this before committing work:
- **Feeder:** won't submit a `/batch` job when the invariant is already at its limit (in addition to its existing `target_depth` reorder-buffer cap, which is unchanged and serves a different purpose — keeping a small reorderable buffer).
- **Arena:** won't start a sweep's `/asr` work when the invariant is at its limit; it waits and surfaces a `waiting_for_capacity` state.

### Worked examples (these become test assertions)

| `N` | Behavior |
|----|----------|
| `1` | A batch job processing → arena waits. An arena sweep running → feeder holds batch. Only ever **one** transcription on the GPU. |
| `2` (default) | One batch + one sweep can coexist; a third waits. |
| `1000` | Condition essentially never true → nothing throttled; feeder feeds to `target_depth`, sweeps fire instantly. |
| `None` (old/unreachable subgen) | Gate **disabled** → behavior identical to today. No regression, no false stalls. |

## Architecture & components

### Piece 1 — `subarr-subgen` (ships in r10)

Add `concurrent_transcriptions` to the `GET /queue` `capabilities` block in `subgen.py`. The value already exists as a module global; this just exposes it. One line plus a comment. No behavior change in subgen — enforcement stays in subarr; subgen merely reports its limit truthfully.

### Piece 2 — `subarr`

**`subgen_client.py`** — parse the new field into `SubgenCapabilities`:
- Add `concurrent_transcriptions: int | None = None` (None when the field is absent → old subgen).

**Shared capacity helper (new, small module or a function in an existing one)** — the single source of the invariant, pure and unit-testable:
```
subgen_capacity_free(*, processing_count: int, arena_in_flight: int, n: int | None) -> bool
    # n is None  -> True  (gate disabled: old/unreachable subgen)
    # else       -> (processing_count + arena_in_flight) < n
```
Both producers call this; the invariant is defined in exactly one place.

**`pending_feeder.py`** — add the capacity gate to `tick()`:
- New injected provider `arena_inflight_provider=lambda: 0` (mirrors the existing `target_depth_provider`/`paused_provider` injection pattern — keeps the feeder decoupled from `ArenaService`).
- `N` comes from the subgen capabilities already probed on the feeder's `subgen` handle.
- Feed only while *both* the existing reorder condition (`effective < target_depth`) **and** `subgen_capacity_free(...)` hold.

**`arena_service.py`** — capacity gate + waiting state:
- New run status `waiting_for_capacity`, emitted between `queued` and `running`.
- In `_run`, after acquiring the sweep semaphore and before transcribing, poll `subgen_capacity_free(...)`; while blocked, set status `waiting_for_capacity`, emit an SSE event (with "N transcription(s) ahead"), and re-check on a short interval until clear, then proceed to `running`.
- Expose `arena_in_flight` (count of runs currently in `running` status) for the feeder's provider.
- The feeder reads subgen's `processing_count`; the arena reads the same via its existing subgen handle.

### Data flow

```
subgen GET /queue  ──(capabilities.concurrent_transcriptions = N)──>  SubgenCapabilities
                   ──(processing_count)──────────────────────────────┐
                                                                      v
ArenaService.arena_in_flight ──> subgen_capacity_free(processing, arena_in_flight, N)
                                       ^                    |
                       feeder.tick() ──┘                    └──> arena _run gate
```

### UI — two surfaces (both required)

**Surface A — Tuning Lab page (the launcher's feedback).** The arena run card already renders status via SSE. Add the `waiting_for_capacity` state: e.g. *"Waiting for subgen capacity — N transcription(s) ahead,"* updating live as it clears, then transitioning to the normal running view. Without this, a gated sweep is a silent stall on the exact page the user is watching.

**Surface B — Queue page (the queue-watcher's awareness).** A lightweight **read-only** indicator (not a queue row — the arena isn't in subarr's `pending_queue`): e.g. *"Tuning Lab sweep running · using 1 GPU slot."* Sourced from the same `arena_in_flight` signal, surfaced on the Queue API response. Lets a user about to fire batch work see the contention instead of firing blind.

## Error handling & degradation

- **`N` is `None`** (old subgen, or `/queue` unreadable): `subgen_capacity_free` returns `True` → gate is a no-op → behavior identical to today. The feature is dormant-safe and lights up automatically once r10 is running.
- **subgen unreachable mid-sweep:** existing arena error handling applies; the capacity poll treats an unreadable queue as "can't confirm capacity" and should fail **open** after a bounded wait (don't hang a sweep forever on a flaky probe) — i.e. proceed rather than stall indefinitely. (Bounded-wait detail to be specified in the plan.)
- **Feeder:** an unreadable subgen queue is already a soft skip; the capacity gate inherits that.

## Sequencing

Build **Piece 2 first** (the bulk, fully testable against a mocked `N`), land it dormant-safe behind the `None` degradation. Then the r10 patch (Piece 1) flips it live with no further subarr change. Either repo order is safe because of the degradation path.

## Testing

- **Capacity helper:** direct unit tests at `N ∈ {1, 2, 1000, None}` × representative `processing_count`/`arena_in_flight` — the Section "worked examples" table, asserted.
- **Feeder:** over a mocked subgen queue + `arena_inflight_provider`, assert it stops feeding when the invariant is hit and resumes when it clears; assert no behavior change when `N=None`.
- **Arena:** assert a sweep enters `waiting_for_capacity` (and emits the SSE event) when the gate is closed at start, and proceeds to `running` when it opens; assert `arena_in_flight` reflects running sweeps.
- **Frontend:** vitest for the run-card waiting state (Surface A) and the Queue indicator (Surface B).

### No mock data in production (hard requirement)

We have been bitten before (telemetry self-pollution from dev/test pings). Explicit gates:
- The mocked `N` / mocked subgen capability used to test Piece 2 **must exist only in tests** — no hardcoded fake `N`, no test-only capability injection on any production code path.
- The `arena_inflight_provider` default (`lambda: 0`) is a *safe real default*, not mock data — but verify production wiring passes the real `ArenaService` provider, not the default.
- Before marking done: grep the diff for any stub/fake/sample value and confirm none reach a runtime path. Surface A/B must render from real arena state, never placeholder copy.

## Files (anticipated)

- `subarr-subgen`: `subgen.py` (`/queue` capabilities block).
- `subarr`: `subgen_client.py`, a capacity-helper (new/existing module), `pending_feeder.py`, `arena_service.py`, the Queue API route (Surface B field), and the Tuning Lab + Queue frontend bundles (Surfaces A/B).
