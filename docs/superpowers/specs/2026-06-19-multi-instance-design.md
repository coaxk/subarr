# Multi-instance (#161) — design

**Date:** 2026-06-19
**Status:** Design approved (architecture), pending implementation plan. Design-only — this is a multi-week, multi-phase epic; no code this session.
**Issue:** [coaxk/subarr#161](https://github.com/coaxk/subarr/issues/161) — "Multiple Sonarr/Radarr/Bazarr instances" (external demand: @KRDucky; reporter runs one arr-set for normal content + one for anime).

## Problem

Today subarr assumes exactly one Sonarr, one Radarr, one Bazarr. A common homelab topology runs **multiple arr sets** — e.g. a "Main" set and an "Anime" set, each with its own Sonarr + Radarr + Bazarr. subarr can only see one set, so the other set's library is invisible: no coverage, no wanted-list ingestion, no write-back.

## Keystone finding (RTFM)

**Bazarr binds to exactly one Sonarr and one Radarr.** Verified against the [Bazarr settings docs](https://wiki.bazarr.media/Additional-Configuration/Settings/) (current, 2026) and the long-standing [multi-instance request #404](https://github.com/morpheus65535/bazarr/issues/404): Bazarr has no multiple-Sonarr/Radarr support, so anyone with multiple arrs **must** run a Bazarr per set. This is decisive: it means the natural unit is a **group** = `{ 1 Bazarr + 1 Sonarr + 1 Radarr }`, and the "single Bazarr + multiple arrs" topology simply cannot exist. Within a group, a Bazarr-wanted item's `sonarrSeriesId`/`radarrId` is unambiguous — the **group is the namespace**, so subarr never needs Bazarr to tag items by instance (which it couldn't anyway).

## Model: instance groups

- A **group** is a named arr set: `{ name, bazarr_url/key, sonarr_url/key, radarr_url/key }`. Multi-instance = N groups (e.g. "Main", "Anime").
- An existing single-instance install becomes the implicit **default group** (empty slug), exactly like #134's "library 0" — zero-config upgrade; env vars keep populating the default group.

### Two orthogonal axes — do NOT conflate (the #1 trap)

Multi-library (#134) and multi-instance (#161) are **different axes**:

| Axis | What it identifies | Mechanism |
|------|--------------------|-----------|
| **Library (#134)** | *Where files physically live* — a filesystem root + its subgen/arr path prefixes | `@slug/` canonical prefix; per-library `arr_prefix`/`subgen_prefix` |
| **Group (#161)** | *Which arr set manages the content* — Bazarr+Sonarr+Radarr identity | new `group_id` |

Relationship: **a library belongs to exactly one group; a group can own several libraries** (many-to-one). This preserves a case #134 explicitly supports — **one Sonarr with root folders across multiple disks** = one group, multiple libraries. Collapsing the axes ("group == library") would break that. Data model: a `group_id` on the `Library` (default library → default group).

**#134 de-risks the hardest part:** path canonicalization is *already* per-library, so once a library knows its group, per-group path resolution and write-back targeting fall out of existing machinery. Multi-instance leverages libraries; it does not reinvent them.

## Scope

**Per-group (multi):** Sonarr, Radarr, Bazarr.
**Single / shared:** Plex, Tautulli, subgen.
- Plex/Tautulli are one media server / one watch-signal source spanning all content; they resolve back to a group via the file's library/path.
- subgen is one GPU worker shared by all groups (see Concurrency).

**Explicitly out of scope (deliberate boundary, not a gap):**
- **Multiple subgens** (e.g. a GPU subgen for Main + a CPU subgen for Anime). That is multiple GPU budgets and a separate, larger epic.
- **Automatic per-group queue fairness** (see Concurrency — manual reorder covers the MVP).
- **Per-item Bazarr instance tagging** — unnecessary; the group is the namespace.

## Concurrency & queue — zero rework required

The concurrency work shipped 2026-06-19 (PR #279 + subgen patch 0023) is **multi-instance-safe by construction**, because it is keyed on the *shared subgen's own state*, not on producers:

- The gate `subgen.processing_count + arena_in_flight < N` (N = subgen's live `CONCURRENT_TRANSCRIPTIONS`) has **one GPU budget** regardless of group count. Multi-instance multiplies arrs, not the GPU.
- The pending-queue stays a **single global holding pen** draining into the one subgen at `target_depth`. Work from all groups merges into it.
- The arena tunes recipes by *language* against the shared subgen — group-agnostic. `arena_in_flight` is global.
- More foreign producers (each group's Bazarr could webhook subgen directly) are absorbed for free: the feeder backs off on subgen's *total* processing, foreign jobs included.

**Queue changes needed (small):**
1. **`group_id` on pending-queue jobs** — so the completion-watcher routes the Bazarr scan-disk to the *originating* group's Bazarr (see Write-back). Derivable from `canonical_path → library → group`, but stored explicitly for clarity.
2. **Group badge on queue rows (UI)** — same `group_id`, so a user can see/reorder by group.

**Fairness:** one global queue means a large group's backlog can starve a small group under fully-unattended operation. MVP answer: **global priority + the existing manual step-wise reorder / pause / priority buckets (#144) + the group badge.** A starved group is never stuck — the user promotes it. Do NOT build automatic per-group round-robin/quota now (YAGNI). Documented escape hatch if unattended starvation is ever reported: a per-group priority bump.

## Write-back routing — the real work

Reads fan out cleanly (iterate groups). The genuine effort is **routing every write-back to the correct group**, where code today targets "the" arr:
- **Completion → Bazarr scan-disk** (`completion_watcher.py`): trigger the *originating group's* Bazarr, not a global one. Wrong group = scan on a Bazarr that's never heard of the file.
- **Audio-language → Sonarr** (`routers/audio_lang.py:_propagate_to_sonarr`): PUT the language update to the *owning group's* Sonarr, resolved via the file's library→group.
- Both resolve through the file's library→group ownership; the pending-queue `group_id` carries it for the completion path.

## Auto-grouping (RTFM win)

Docker discovery already enumerates all containers. To assemble them into groups without hand-linking: **read each discovered Bazarr's own Sonarr/Radarr config** (Bazarr stores and exposes the arr URL it is bound to) → "this Bazarr points at that Sonarr ⇒ same group." A small Bazarr-API check during implementation (same RTFM discipline as the wanted-list research) confirms the exact endpoint/field. Fallback: manual grouping in the wizard.

## Gotchas catalogue

1. **Don't conflate library and group axes** — group owns 1+ libraries (many-to-one). The #1 trap.
2. **Retire the global `ARR_PATH_PREFIX`** — it can't represent two groups' path mappings. #134 already moved prefixes per-library; the global scalar survives only as the default-group/default-library value.
3. **Write-back must route by originating group** (completion → Bazarr, audio-lang → Sonarr). Reads are easy; routing is the work.
4. **Shared Plex/Tautulli/subgen resolve across groups by path/library** — a finished job or watch signal maps back to its group via library. A seam to test.
5. **Clean ID-qualification** — one canonical scheme (`group_id` on provenance/coverage/queue). Do not double-namespace with the library `@slug`.
6. **ID collisions** — `sonarr_episode_id`/`radarr_movie_id` are unique only within a group; group-qualify them in provenance + coverage rows.
7. **One subgen, not one-per-group** — stated boundary above.

## Blast radius (from architecture map)

Six load-bearing subsystems, ranked by risk:
1. **Configuration** (`config.py`, `config_store.py`, `routers/integrations.py`, `settings.jsx`) — scalar fields → list of groups; persistence + UI edit flow.
2. **Client lifecycle** (`coverage_engine.py: IntegrationBundle`, `app.py` lifespan) — one client per service → a registry keyed by group.
3. **Data model / ID uniqueness** (`CoverageItem`, `provenance.LedgerEntry`, `pending_queue`) — add `group_id`; group-qualify arr IDs.
4. **Coverage engine** (`build_coverage`) — iterate groups; merge with group qualification; per-group Bazarr wanted-list.
5. **Write-back paths** (`completion_watcher.py`, `routers/audio_lang.py`) — route to the originating group.
6. **Path canonicalization** (`paths.py`) — retire global prefix in favour of per-library/per-group.

## Phased breakdown (each phase shippable + testable; dormant for single-group installs until used)

- **Phase 0 — data model.** Introduce the group entity + `group_id` on `Library`, provenance, coverage rows, pending-queue jobs. Create the default group; migrate existing config into it. No behavior change (one default group). Group-qualify arr IDs.
- **Phase 1 — config + client registry.** Settings holds a groups list; `IntegrationBundle` becomes a registry keyed by group; env vars populate the default group. In-app add/edit of a group's credentials. Read path works for N groups.
- **Phase 2 — coverage engine iterates groups.** `build_coverage` loops groups, reads each group's Bazarr wanted-list + Sonarr/Radarr metadata, merges with group qualification (no cross-group ID collisions).
- **Phase 3 — write-back routing.** Completion → originating group's Bazarr; audio-lang → owning group's Sonarr. Uses the `group_id` on jobs + library→group ownership.
- **Phase 4 — UI + auto-grouping.** Settings: add/manage groups. Onboarding: auto-assemble groups by reading each discovered Bazarr's arr config. Queue rows: group badge.

Migration safety throughout: a single-group (default) install behaves exactly as today at every phase.

## Open items for the implementation-planning session

- Confirm the Bazarr endpoint/field that exposes its bound Sonarr/Radarr (auto-grouping, Phase 4) — RTFM before building.
- Decide the exact `group_id` representation (slug string vs int) and how it appears (if at all) in canonicals vs staying a side-column.
- Settings/onboarding UX for N groups (card-per-group? a groups manager?) — a visual pass when Phase 4 is planned.
