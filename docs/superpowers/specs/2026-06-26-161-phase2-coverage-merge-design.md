# #161 Phase 2 — Coverage merge across instances (read path) — Design

**Issue:** [coaxk/subarr#161](https://github.com/coaxk/subarr/issues/161) (author: @KRDucky)
**Date:** 2026-06-26
**Status:** Design approved (Approach A); ready for implementation planning.
**Builds on:** Phase 1 (merged, `680b206`) — `instances.py`, per-service `IntegrationBundle` dicts, `client_for`/`clients_for`, library `sonarr_id`/`radarr_id`/`bazarr_id` bindings, `library_for_canonical`. Parent design: `2026-06-25-161-multi-instance-design.md` (§5 seams, §8 phasing).

---

## 1. Scope

**Read path only.** Make `build_coverage` (`coverage_engine.py:1354`) fan out across all
Sonarr/Radarr/Bazarr instances and merge their wanted-list + library data into one
coverage view, with rows correctly attributed to their library. Dedup re-key (seam 4)
is included. **Out of scope (Phase 3):** the ~14 writeback sites (subtitle upload,
blacklist, episodeFile language PUT) + the per-Bazarr `_bazarr_sync_task_id` cache —
those stay on instance 0 this phase. **Byte-identical for single-instance installs.**

## 2. Confirmed target topology (@KRDucky, 2026-06-26)

- 2 Sonarr (TV → `/data/tv`, Anime → `/data/anime`), 2 Radarr (Movies → `/data/movies`,
  Anime Movies → `/data/animefilms`). **One root folder per arr instance** → clean
  library↔arr 1:1.
- 2 Bazarr, each fronting a **pair**: `Bazarr` = `/data/tv` + `/data/movies`;
  `Bazarr-anime` = `/data/anime` + `/data/animefilms`. So a Bazarr is **per-stack, not
  per-arr** — its wanted list spans a Sonarr's library AND a Radarr's library.
- Single mount (`/mnt/tank/media`, ZFS) + trash-guides subfolders. Each instance has its
  own port + API key.

## 3. Key finding — path attribution is already solved

Coverage rows derive their canonical via `strip_arr_prefix(arr_path)`, which is
**library-aware** (#134): each library's unique `arr_prefix` resolves a path to the
correct `@<slug>` canonical. So a Bazarr wanted item's *path* auto-attributes to its
library with no extra tagging — `Bazarr-anime`'s `/data/anime/...` and
`/data/animefilms/...` items resolve to the `anime` and `animefilms` libraries
respectively. File-based dedup (`seen_files`, keyed by `@slug` canonical) is therefore
**already instance-safe**.

**The actual risk is raw arr IDs colliding.** `build_coverage` keys many intermediate
lookups by raw IDs — `sonarr_eps_by_id`, `ep_file_paths` (by `episodeFileId`),
`eps_by_series_id`, and the ID-based `seen_ep_ids`. Episode 101 exists in *both* Sonarrs;
merging instances into one global pass makes these dicts collide and silently match the
wrong instance's data.

## 4. Approach A — per-instance assembly, then merge (APPROVED)

Rather than one global pass with `(instance,id)` re-keying everywhere (Approach B,
rejected — touches every ID site, one miss = silent wrong-instance match), assemble
coverage **per media-manager instance in its own scope**, then concatenate the resulting
`CoverageItem` lists.

- **Episodes:** for each Sonarr instance, assemble episode coverage from that Sonarr's
  series/episodes/files + the Bazarr-episode-wanted items that map to that Sonarr's
  libraries. Raw episode/series/file IDs live only within one instance's scope, so the
  lookup dicts never collide.
- **Movies:** symmetric, per Radarr instance.
- **Bazarr wanted lists:** fetched once per Bazarr instance, then **distributed to the
  arr-assemblies by library** (each item's path → `library_for_canonical` → the
  library's `sonarr_id`/`radarr_id`). A per-stack Bazarr's items naturally split: episode
  items go to the Sonarr-bound library's assembly, movie items to the Radarr-bound one.
- **Merge:** concatenate the per-instance `CoverageItem` lists. Items already carry
  `@slug` canonicals → globally unique → safe to merge with no cross-instance key clash.
  The "stack" grouping KRDucky described is implicit: libraries sharing a `bazarr_id`.

**Single-instance back-compat:** with one instance per service, the per-instance loop
runs exactly once over instance 0 with identical inputs → byte-identical output. Guarded
by a characterization test (same bar as Phase 1).

## 5. Components (decomposition)

`build_coverage` is ~650 lines and already large; Phase 2 is the natural point to extract
its per-stack assembly into a focused helper rather than grow it further:

- **`_fetch_bazarr_all(bundle, sources)`** — iterate `bundle` bazarr instances, fetch
  each `episodes_wanted()`/`movies_wanted()`, return items tagged with their source
  bazarr instance id (for `sources` health reporting per-instance) + their resolved
  library slug (via `library_for_canonical` on the item path).
- **`_assemble_stack_coverage(sonarr_client, radarr_client, bazarr_items_for_stack, ...)`**
  — the existing per-pass assembly logic, parameterised by the instance clients + the
  Bazarr items routed to this stack's libraries. Produces `CoverageItem`s.
- **`build_coverage`** — orchestrator: resolve the instance→library groupings from
  `settings.instances` + `settings.libraries` bindings, fan out `_assemble_stack_coverage`
  per group (bounded concurrency, mirroring the existing `Semaphore(8)` fan-out), merge.
- **Dedup (seam 4):** `seen_ep_ids` becomes scoped per-assembly (no global mixing) OR
  keyed `(instance_id, ep_id)` if a global set is retained; `seen_files` (canonical) is
  unchanged. Decide concretely in the plan against the final structure.

Each unit is independently testable: `_fetch_bazarr_all` (multi-instance fetch + tag),
`_assemble_stack_coverage` (one stack → items), the grouping resolver (bindings →
instance groups), and the merge.

## 6. `sources` / health reporting

`sources["bazarr"]`, `["sonarr"]`, `["radarr"]` currently hold one ok/error blob each.
With N instances, surface **per-instance** status (e.g. `sources["bazarr"]["instances"]`
= list of `{id, ok, configured, episodes_wanted, movies_wanted, error?}`) so the
coverage-degraded banner (#286) can report *which* instance is unreachable, not just
"Bazarr". Keep a top-level rollup (`ok = all configured instances ok`) for existing
consumers.

## 7. Error handling

Per-instance failures degrade gracefully and independently: if `Bazarr-anime` is
unreachable, its libraries' coverage degrades (banner flags it) while the `Bazarr`
stack's coverage is unaffected. Mirror the existing `IntegrationError` → `sources[...]
.ok=false` pattern, per instance. A single bad instance never sinks the whole refresh.

## 8. Testing

- **Characterization (single-instance byte-identical):** capture current `build_coverage`
  output for the single-stack fixture before the refactor; assert identical after. CI gate.
- **Multi-instance (using the `anime_stack`-style fixtures from Phase 1, extended):** two
  Sonarr + two libraries; assert episode 101 in both instances produces two distinct
  rows with correct `@slug` canonicals (no collision); assert a per-stack Bazarr's wanted
  items route to the right libraries.
- **Per-instance health:** one instance erroring degrades only its libraries; `sources`
  reports per-instance status.
- **Dedup:** same raw ep_id across instances does not cross-suppress.

## 9. Open items / for the plan

- Exact dedup structure (`seen_ep_ids` scoped vs `(instance,id)`-keyed) — pick against the
  final assembly shape during planning.
- Whether `_assemble_stack_coverage` extraction is one big move or a few smaller helpers
  (the plan decides task granularity; keep functions focused).
- Bounded concurrency for the per-stack fan-out (reuse the existing semaphore pattern).
