# #161 — Multiple Sonarr/Radarr/Bazarr instances — Design

**Issue:** [coaxk/subarr#161](https://github.com/coaxk/subarr/issues/161) (author: @KRDucky)
**Date:** 2026-06-25
**Status:** Design approved; ready for implementation planning.
**Supersedes:** the 2026-06-11 recon comment on #161 (this design replaces its "instance-tag every coverage row + row migration" approach with a slug-provenance approach — see §3).

---

## 1. Problem

subarr holds exactly one Sonarr, one Radarr, and one Bazarr client. Users who run
**separate "normal" and "anime" stacks** (one Sonarr/Radarr/Bazarr each) cannot
point subarr at both. KRDucky's setup is the canonical case: paired stacks with
trash-guides mounts (`/data/{tv,anime,movies,animefilms}`). subarr is the *only*
component in that topology that naturally sits above the split arr trios, so
aggregating them is squarely its job.

## 2. Scope boundary — what actually multiplies

subarr integrates **seven** services (`coverage_engine.py:227` `IntegrationBundle`).
Only the media-manager trio splits per-stack:

- **Multiplies (the #161 work):** Sonarr, Radarr, Bazarr → per-stack lists.
- **Stays singleton (untouched):** Plex (one server, N library *sections*),
  Tautulli (1:1 with Plex), subgen (one transcoder), ollama (one analysis backend).

Nobody runs two Plex servers for an anime split — one Plex holds an Anime library
and a TV library. **Multi-Plex / multi-subgen is explicitly out of scope** (a
separate future epic if real demand ever appears).

## 3. Core insight — the library is the provenance carrier

#134 made every coverage and queue row keyed by a canonical path that already
embeds its library via the `@<slug>/` head (`paths.py:_split_canonical`,
`fs_to_canonical`; library 0 = empty slug). **So instances do not need a parallel
per-row tag.** A row's `@anime/…` head → library *anime* → its bound instances.

This is the central simplification over the 2026-06-11 recon, which proposed adding
`instance` columns to coverage rows (a real DB migration on every install). We don't:
**there is no row/schema migration** — existing rows are byte-for-byte untouched.

### Three layers

1. **Instances** — flat per-service credential lists (`sonarr[]`, `radarr[]`,
   `bazarr[]`). Instance 0 = today's env-backed scalars.
2. **Libraries** (#134, already exist) — `slug · fs_root · subgen_prefix · arr_prefix`.
   Instances bind *here*.
3. **Rows** — canonical key `@slug/path` already carries the library, hence the
   binding, hence the owning instances.

## 4. Data model

### 4.1 Instance model (new `instances.py`)

Pure, unit-testable module mirroring `libraries.py` (no env, no IO, no `settings`
import — `config.py` owns the IO seam and fail-soft load wrapper):

```python
@dataclass(frozen=True)
class Instance:
    id: str        # immutable slug; "" = instance 0 (env-backed legacy)
    service: str    # "sonarr" | "radarr" | "bazarr"
    name: str       # human label, e.g. "anime"
    url: str
    api_key: str
```

- `build_instances(default, extras)` validates non-empty unique ids; `""` reserved
  for instance 0. Direct copy of `build_libraries`'s shape including **fail-soft
  fallback to single-instance** on bad config.
- Stored in the override store (the same store `libraries[]` extras use) under one
  `instances` key → `{sonarr:[...], radarr:[...], bazarr:[...]}`.
- Instance 0 of each list is **synthesised from the existing scalars**
  (`sonarr_url`/`sonarr_api_key`/… at `config.py:126-131`) so an existing install
  boots with `[instance0]` and is byte-identical.

### 4.2 Library binding fields

`Library` (`libraries.py`) gains: `sonarr_id`, `radarr_id`, `bazarr_id`.

- A library uses exactly **one** media-manager (Sonarr *or* Radarr, by content
  type) plus **one** Bazarr.
- Empty/unset = "resolve against instance 0" → preserves single-instance back-compat.
- Binding is **auto-derived** from each instance's `root_folders()` on add, stored,
  and **overridable** (the agreed hybrid).

### 4.3 Library granularity rule (important)

A subarr library keys on a **path** = an **arr root folder**, *not* a mount. The
model is mount-agnostic (`fs_to_canonical` longest-prefix match), so both topologies
work:

- Separate mounts (`/mnt/tv`, `/mnt/anime`).
- **Single mount + content subfolders** (trash-guides: one `/data` mount,
  `/data/media/{tv,anime}` subfolders — the *recommended* layout, enabling
  hardlinks/atomic-moves). This is the common case.

**Rule:** libraries are defined at **root-folder granularity, one library per arr
root folder** — not at mount granularity. A coarse mount-level library that
*contained* root folders from two different arrs would span two instances and break
the 1-arr-per-library binding. We don't rely on users getting this right:
**#134 Phase 0 already shipped `/onboarding/root-folders`** (`onboarding.py:368`),
documented as "the ground truth Phase 1's multi-library auto-detect builds on." The
auto-flow enumerates every root folder across all configured arrs and proposes one
pre-bound library each.

**Cardinality:** instance → library = **1:N** (a Sonarr with `/data/tv` and
`/data/tv-4k` = two libraries); library → arr = **1:1** (by construction).

### 4.4 Migration

No SQL/row migration. The only migration is **config-shape** seeding at load:
`rebuild_instances()` paralleling #285's `rebuild_libraries()` — idempotent,
fail-soft. An `INSTANCE_DEFINING_FIELDS` sibling to `LIBRARY_DEFINING_FIELDS`
(`config.py:320`) makes runtime credential edits rebuild the instance list live
(no restart).

## 5. Routing — the four risky seams

One resolution helper underpins everything; the slug is already on every row/path:

```python
def clients_for(canonical) -> (media_mgr_client, bazarr_client):
    lib = library_for_canonical(canonical)            # paths.py already splits @slug
    arr = instance_client(lib.sonarr_id or lib.radarr_id)
    baz = instance_client(lib.bazarr_id)
    return arr, baz
```

`IntegrationBundle` changes from one client per arr service to **per-service dicts
keyed by instance id**, with `bundle.sonarr` / `.radarr` / `.bazarr` retained as
**instance-0 alias properties** (`self.sonarr_instances[""]`). This keeps the ~52
read sites compiling unchanged; only owner-routing sites switch to `clients_for`.

| # | Seam | Location | Fix |
|---|------|----------|-----|
| 1 | Bazarr wanted-list merge (riskiest) | `coverage_engine.py:~1281` | Iterate `bazarr` instances; **tag each item by the instance it was queried from** before path→canonical. Unique `arr_prefix` (#285) makes the resolution deterministic. "Episode 101 in both Sonarrs" collision dies — we key by `@slug/path`, never raw arr id. |
| 2 | Writebacks route by owner (safety-critical) | `completion_watcher.py:~297`, `blacklist.py`, `audio_lang.py:~154` | Fire at `clients_for(row.canonical)` instead of the global client. Wrong-instance metadata corruption becomes structurally impossible. |
| 3 | `_bazarr_sync_task_id` process-global | `audio_lang.py:189` | Becomes a **dict keyed by bazarr instance id**; each Bazarr caches its own task id. |
| 4 | Dedup sets collide | `seen_ep_ids`, `radarr_by_title` | Re-key from `id` to `(instance_id, id)`. |

## 6. Single-stack back-compat & safety (first-class concern)

Single-stack is the majority. "No breakage for single-stack" is a **CI-enforced
invariant**, not a hope. Three structural mechanisms:

1. **Instance-0 alias** keeps all 66 client-consumer sites untouched; only the ~14
   writebacks + the coverage merge loop switch to `clients_for`. We are *not*
   rewriting 66 sites in lockstep (the trap).
2. **No row/schema migration** — `@slug` already carries provenance; existing rows
   untouched. The only migration is idempotent, fail-soft config seeding.
3. **Absent-config = today's exact path** — no `instances` key → seed instance 0
   from scalars → every binding id empty → `clients_for` returns instance 0 → dedup
   keys `("", id)` ≡ `id` → coverage loops over one Bazarr. **Identical output and
   writeback targets.**

The one real (non-behavioural) risk is that the refactor *touches* load-bearing
code (`IntegrationBundle`, coverage merge). Mitigation — prove, don't assert:

- **Characterization tests at a byte-identical bar** (same bar #134 held): capture
  current single-instance coverage-merge output / writeback routing / dedup *before*
  the refactor; assert identical *after*. CI gate.
- **Phase 1 ships inert** — plumbing proven byte-identical before any multi-instance
  config is reachable.
- **Live dogfood on :9923** with crash telemetry (#157 P2) as the rollout net.

## 7. UI

Three surfaces; the add/edit/test widget is **built once** and mounted in both
Settings and the wizard.

1. **Settings ▸ Instances** — per-service lists. Each row: name · url · health, with
   Test / Edit / Remove. Instance 0 = env-backed default, editable but **not
   removable**. On add: url + api-key + name → live **Test** → on save, root folders
   auto-enumerate and propose/refresh libraries.
2. **Settings ▸ Libraries — resolved-topology table** — per library: bound arr +
   Bazarr (auto-filled, overridable dropdowns) and a **derived, read-only Plex
   section** column. Plex section is *not* stored — it is matched live by
   `integrations/plex.py:_section_for_path`; a **no-match ⚠** surfaces a mis-mounted
   library at config time. (Decided: display the Plex link, don't store it.)
3. **Wizard — optional skippable branch** (option C). First-instance flow unchanged;
   after the first stack tests green, a collapsed *"Run separate stacks? + Add
   another | Skip ›"* appears. Skip = identical to single-instance onboarding.

Rejected: by-stack grouping cards — reintroduces the stack-symmetry assumption
(breaks for e.g. 3 Sonarrs behind 1 Bazarr). Flat per-service lists chosen throughout;
the stack *grouping* remains visible where it matters (the resolved-topology table,
grouped by library).

## 8. Phasing

One reviewed PR per phase; **byte-identical for single-stack through Phase 3**; each
independently shippable. The ordering enforces one safety invariant: **the UI that
lets a user create a second instance (Phase 4) lands only after both read (P2) and
write (P3) routing exist** — so it is never possible to add a second Bazarr and have
subs silently upload to the wrong one.

- **Phase 1 — Inert plumbing.** `instances.py`; `rebuild_instances()`;
  `IntegrationBundle` per-service dicts + instance-0 alias; `clients_for` +
  empty-defaulting binding fields. Characterization tests (CI gate). No second-instance
  UI. Ships inert.
- **Phase 2 — Read path.** Multi-Bazarr wanted-list merge with tag-by-queried-instance
  (seam 1); dedup re-key (seam 4). Reads route by owner. Tested by hand-seeding a
  second instance. Ships inert.
- **Phase 3 — Write path (safety-critical).** `clients_for` at all writeback sites
  (seam 2); per-Bazarr task-id cache (seam 3). **Full Tier-2 review** (auth/concurrency
  + failure-mode lens, ultra at milestone). After this, multi-instance is safe to expose.
- **Phase 4 — Expose it.** Settings ▸ Instances + Libraries binding UI +
  resolved-topology + root-folder auto-enumerate. First point a second instance is
  user-creatable. **Live dogfood on a dedicated anime-stack test environment + KRDucky
  beta.**
- **Phase 5 — Wizard branch + docs + announce.** Option C's skippable branch (reusing
  the Phase 4 component) + docs + the anime-hook release note. Lowest risk, last.

## 9. Testing strategy

- **Characterization (Phase 1):** single-instance byte-identical bar on coverage merge,
  writeback routing, dedup — CI gate.
- **Unit:** `build_instances` (dup/empty id, fail-soft), `clients_for` resolution
  (empty binding → instance 0; explicit binding → correct client), seam-1 instance
  tagging, seam-4 `(instance_id, id)` keying.
- **Endpoint/persistence:** instance add/test/remove, override-store round-trip,
  `rebuild_instances` runtime reactivity.
- **Live (Phase 4-5):** a dedicated **anime-shaped topology** test environment (2×
  Sonarr/Radarr/Bazarr) on the dev box, plus **KRDucky as beta partner** validating on
  his real stack — the strongest net for the safety-critical write path.

## 10. Strategic context (non-architectural)

Multi-instance is the **price of entry for the anime userbase** — a large segment
subarr currently locks out entirely. Separate anime stacks are near-universal there
(absolute numbering, alternate-title matching, AnimeBytes/Nyaa indexers force a
dedicated Sonarr). This:

- **Validates the flat-lists decision** — anime users have the least symmetric setups
  (sometimes 2-3 anime Sonarrs); by-stack grouping would have broken for them.
- **Has ready-made demo hooks** — subarr's forced-sub handling (#317) and audio-language
  verification land hardest on the JP-audio + EN-sub + signs/songs anime workflow.
- **Flips the launch's deliberate anti-anime framing** (see launch-prep notes) — not by
  pivoting to anime, but by finally not excluding them.

This changes no architecture (Section 5 already serves it); it raises the stakes on
getting write-path routing right and adds the anime test env + KRDucky beta to Phase 4.

## 11. Open assumptions (flag if wrong)

- **One media-manager arr per library** — holds by construction given root-folder
  granularity (§4.3). Would only break if a single library were deliberately defined
  to span two arrs' root folders; the auto-enumerate flow steers away from this.
- **Plex/Tautulli/subgen/ollama remain singleton** for the foreseeable future.
- **Bazarr fronts only its stack-mate arrs** — used by seam 1's tag-by-queried-instance
  resolution. True for the paired-stack topology; a Bazarr fronting arrs across stacks
  would still resolve correctly via path→slug (the tag is a disambiguation aid, not the
  sole key).
