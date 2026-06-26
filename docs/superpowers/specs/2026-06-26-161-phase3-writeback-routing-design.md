# #161 Phase 3 — Writeback routing across instances — Design

**Goal:** Every write subarr makes to an external arr/Bazarr instance targets the
instance that *owns the row's library*, not always instance 0. Phase 2 made the
READ path (coverage build) per-instance; Phase 3 does the same for WRITES.

**Status:** design (sign-off given 2026-06-26 — scope = all writeback ops; defer
movie-subtitle-upload stub to its own issue; thread `canonical_path` through
request bodies for path-less endpoints).

---

## Principle

There is already one resolver — `clients_for(bundle, canonical) -> ResolvedClients`
(coverage_engine.py) — that maps a row's `@slug` canonical → `Library` → bound
instance ids → clients. **Every writeback resolves its target through it (or
`bundle.client_for(service, instance_id)`).** When a call site has no canonical
path and no binding resolves, it falls back to instance 0 (`""`), so
single-instance behaviour is byte-identical.

The entire effort is: (a) make instance resolution the default at each call site,
and (b) plumb the canonical path to the call sites that don't have it.

**Why not resolve from the raw arr id server-side** (looking the id up in the
coverage snapshot): the same raw `sonarrSeriesId` / `radarrId` legitimately exists
in two instances (the exact collision Phase 2 solved). An arr id is not unique
across instances, so it cannot disambiguate the target. The canonical path (with
its `@slug` head) is the unique key. Hence we thread the path.

---

## The writeback surface (11 ops, from the Phase-3 surface map)

### Tier A — canonical path already at the call site (mechanical)
| Op | File | Today | Change |
|----|------|-------|--------|
| Subtitle upload (episode) | completion_watcher.py:~301 | `self._bazarr` (inst 0) | `clients_for(bundle, entry.canonical_path).bazarr` |
| Sonarr episodeFile language PUT | audio_lang.py:~157 | `bundle.sonarr` | `clients_for(bundle, canonical_path).sonarr` |
| Bazarr "scan-disk" trigger (completion main + retry) | completion_watcher.py:~272,418 | `self._bazarr` | resolve per `entry.canonical_path` |
| Bazarr trigger after audio-lang propagate | audio_lang.py:~220 | `bundle.bazarr` | resolve per `canonical_path` (thread into `_trigger_bazarr_sync`) |

### Tier B — request schema lacks a path (add `canonical_path`, UI supplies it)
| Op | File | Today | Change |
|----|------|-------|--------|
| Bazarr blacklist (episode) | blacklist.py:~156 | `request…bazarr` | add `canonical_path` to request; resolve |
| Bazarr blacklist (movie) | blacklist.py:~175 | `request…bazarr` | same |
| Bazarr download-candidate accept (episode) | arbiter.py:~106 | `request…bazarr` | add `canonical_path`; resolve |
| Bazarr download-candidate accept (movie) | arbiter.py:~116 | `request…bazarr` | same |
| Bazarr sync-disk standalone trigger | bazarr_sync.py:~71 | `bundle.bazarr` | use the (already-optional) `canonical_path`; resolve; fallback inst 0 |

The UI already has the row — including the Phase-2 `library` label and the row's
`@slug` canonical — when it fires these actions, so it can pass `canonical_path`.
Field is **optional**; absent → instance 0 (back-compat for any external caller).

### Tier C — cross-cutting
1. **Per-instance Bazarr task-id cache.** The "sync disk" task id is discovered
   once and cached: `_bazarr_sync_task_id` (module global in audio_lang.py) and
   `completion_watcher._bazarr_task_id` (instance attr). Task ids may differ per
   Bazarr instance → replace each with a `dict[str, str]` keyed by bazarr
   instance id, discovered lazily per instance.
2. **Library-wide trigger fan-out.** The scheduler's stale-disk poke
   (scheduler.py:~298) triggers a whole-library scan once on instance 0.
   Multi-instance → trigger each Bazarr instance that owns at least one stale
   item (derive the instance set from the stale rows' canonicals). Single-stack:
   one instance → identical.

### Deferred (own issue)
- **Movie subtitle upload** (completion_watcher.py:~310) is a stub that falls
  back to scan-disk because `radarr_movie_id` isn't populated on the provenance
  entry. Wiring it is unrelated provenance work; file separately, not in Phase 3.

---

## Back-compat & safety

- Single-instance: every `clients_for(...)` resolves to instance 0 → identical
  calls. A "writeback characterization" test asserts the instance-0 client is the
  one called for a single-stack setup.
- New `canonical_path` request fields are optional; missing → instance 0.
- Task-id cache: single instance → one-entry dict, identical discovery.
- The resolver already exists and is unit-tested; Phase 3 adds no new routing
  math, only call-site wiring + path threading.

## Test strategy

A multi-instance writeback fixture: two Bazarr + two Sonarr instances whose mock
transports RECORD which instance received each write. Each task asserts the write
landed on the instance bound to the row's library (e.g. an `@anime/...` row's
blacklist hits the `anime` Bazarr, a default-library row hits instance 0). Plus a
single-instance assertion that instance 0 is used (byte-compat).

## Open questions (resolved)
- Scope: ALL writeback ops (Tier A+B+C 1-2); movie-subtitle-upload deferred. ✓
- Path-less endpoints: add optional `canonical_path`, UI supplies it. ✓
- Task ids per instance: cache keyed by bazarr id. ✓
