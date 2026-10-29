# Relink stale paths before pruning (#638)

## Intent

The orphan prune (#453) protects verifications from a dropped mount with a
ratio guard: above 50% of known paths missing, it refuses to delete anything.
#638 is the case it handles badly. The reporter's rows were recorded while their
mounts were misconfigured, so 15,008 of 17,052 paths (88%) point at a layout
that no longer exists. The mount is healthy now, since about 2,000 paths resolve,
but the guard refuses on every run, re-walking cannot clear it, and nothing in
the UI moves it.

Those rows are not garbage. Each audio-language verification is hand-confirmed
work, and after a mount fix or a library move **the files are usually the same
files at a new path**. So the fix is not a way to prune faster. It is a way to
**find each file at its new path and move the row onto it**, pruning only what
genuinely cannot be found.

## Goals

- Detect that stale rows map onto current files under a consistent path
  rewrite, and offer that rewrite as a **rule** the user applies in **one
  click**, not a per-row decision. The user never types a path.
- Relinked rows keep everything: the verification's language, source,
  confidence, date and arr ids (the existing `_move_verdict` behaviour), and the
  probe cache row.
- After relinking, the existing prune guard judges only what is left. For the
  #638 reporter that is a small remainder, and the normal prune flow proceeds.
- A real mount drop behaves exactly as today. Nothing can match because the new
  paths are not readable either, so nothing relinks and the guard refuses.

## Non-goals

- **No "prune anyway" / force override on the ratio guard.** Relinking removes
  the need for one, and a force button on a data-loss guard is the outcome to
  avoid (Claude's call, stated to Judd and not objected to).
- No re-probe or audio fingerprint to confirm identity. Path plus size is the
  bar (Judd).
- No change to the 50% ratio or `MIN_TOTAL_TO_JUDGE`.
- No automatic or scheduled relink. It happens only on the user's click.

## Anti-goals

- A rule that moves a verification onto a *different* file. Ambiguity resolves
  to "unmatched", never to a guess.
- Any write during the dry run.
- Stalling the event loop: this stats paths, and on a network share that is
  slow. It runs in a thread like the existing `_sweep`.

## Decided

**Matching (Judd: path + size, keep it simple).**
- A stale path S matches a current file C when they share a **tail of at least
  3 path segments** (filename, its parent and grandparent, for example
  `Show/Season 1/ep.mkv`). Take the longest shared tail. The rule is then
  `head(S) -> head(C)`, the segments before the shared tail, including any
  `@slug` head. This one form covers dropped segments, added segments and a
  library change.
- **Size**: when a `media_probe` row exists at the OLD path, its `size` must
  equal the current file's size or there is no match.
- **Path-only (Judd: relink them too)**: a stale row with no probe row at the old
  path has no size to compare. It still matches on the tail, and is **counted
  separately** in the rule ("N by path + size, M by path only") so the split is
  visible before the click.
- **Ambiguous**: if S's tail matches more than one current file, S is unmatched.
- **Rule threshold**: a rule is offered only with at least **3 matched rows**,
  so a coincidental filename hit cannot propose a rewrite (Claude's call).

**What a relink does.**
- The verification is re-keyed with the existing `_move_verdict` (it UPDATEs in
  place and records provenance in `evidence`). The evidence key should say it
  was a relink rather than #563's carry-over, for example `relinked_from`, so
  the two stay distinguishable.
- If a verification already exists at the new path, **the existing one wins**:
  the stale row is not merged and stays as a prune candidate. `_move_verdict`
  already refuses that case with `NOT EXISTS`.
- The probe row at the old path is re-keyed to the new path too, under the same
  collision rule. A stale probe row with no verification is cheap and
  regenerable, but moving it keeps the size evidence and avoids a re-probe.

**Order.** Relink is a separate, explicit apply step that the user clicks. The
prune's dry run and guard are computed on the state after any relinks the user
has applied, so the existing banner flow stays intact: relink cards first, then
the prune summary for whatever remains.

**UI.** The existing `OrphanBanner` in `review.jsx`. Above the current
prune/refusal text, one card per offered rule: old head, new head, matched count
with the path+size / path-only split, a sample of about 5 pairs, and the full
list one expand away. Buttons: **Relink all** and **Skip**. After a relink, the
banner re-fetches from the server to prove the result. It does not trust the
POST echo, the same principle as the existing prune. When the remainder is
under the guard, the normal prune offer appears. When it is still over, the
existing refusal text shows. Single rule or many, the shape is the same.

## Constraints

- Stats and file walks run off the event loop (`asyncio.to_thread`), as `_sweep`
  does.
- An unresolvable path (library removed, traversal guard) counts as PRESENT for
  pruning, as `_path_exists` already does. A relink target must be a resolvable,
  existing file.
- Any SQL work stays in the stores. No hydrating whole tables into Python per
  request (the #621 / #626 class).
- Logs scrub paths (`scrub`), as `_move_verdict` does.
- Endpoints sit beside `/admin/db/orphans` in `routers/admin.py`. The dry run is
  a GET and writes nothing. Apply is a POST that takes the rule (old head, new
  head) and **recomputes the matches server-side**, never trusting a client
  list.

## Settled after the cold spec check

Each item below is Claude's call unless marked otherwise.

- **Path-only guardrail.** Path-only rows (Judd: relink them) are carried only
  by a rule that has at least one **size-confirmed** match and **zero size
  mismatches**. A rule with any mismatch, or with no size-confirmed match at
  all, offers only its size-confirmed rows, and its path-only rows stay
  unmatched. A rewrite proven on size-checked siblings makes the path-only rows
  under it near-certain. A rule without that proof does not move unverified
  files. The card lists path-only pairs in their own expandable section.
- **Matching details.** Exact, case-sensitive segment comparison on
  `/`-normalised canonical paths. The `@slug` head is part of the head, never a
  tail segment. A stale path with fewer than 3 segments cannot match. Filter
  candidates by size first, then apply the ambiguity test, so a size mismatch
  never makes a path look ambiguous. A probe `size` of `None` counts as
  path-only. An arr-sourced probe size (`probe_store.py` `source`) counts as a
  size.
- **Rule identity and grouping.** Rows group by the exact
  `(head(S), head(C))` pair. A head may be empty (the default library root). The
  POST takes that pair, and the server recomputes the matches. The threshold
  counts **distinct paths that have a verification**. Probe-only stale rows
  under an applied rule are relinked too but do not count toward it.
- **`_move_verdict` reuse.** Add a parameter for the provenance key and log
  wording, so a relink writes `relinked_from` / `relinked_at` and logs as a
  #638 relink. #563 carry-over keeps `carried_from` / `carried_at` unchanged.
- **Probe re-key is new code.** `probe_store.py` has no move today. Add one with
  the same NOT-EXISTS collision rule. A matching `probe_failures` row moves with
  it. A re-keyed row is still validated by `get()` on size and mtime, so it is a
  cache hit only for an unchanged file, which is the intent.
- **Carry-over interaction.** Unchanged: `_sweep` still runs #563/#571
  carry-over before a real prune. Relink is a separate apply, and a row already
  carried over is no longer stale, so it never appears in a rule.
- **Scope of stores.** Only the two stores `_orphan_stores` already sweeps
  (`audio_lang`, `probe`). Other `canonical_path`-keyed tables are out of scope.
- **Apply semantics.** Idempotent: re-running a rule moves nothing new.
  Collisions and failures are skipped and counted in the response (`relinked`,
  `skipped_collision`, `failed`).
- **UI.** Relink cards render whether or not the prune is blocked. Skip is
  session-only state. Dismiss and the `n === 0` hiding are unchanged.

## Left to the builder

- **The inventory of current files** that tails are matched against: reuse an
  existing source (the coverage snapshot or the probe cache's present rows) if
  it is complete enough, otherwise a threaded walk of the libraries' roots. Say
  which, and why, in the PR.
- The endpoint names and payload shapes, the indexing approach for tail lookup,
  and the exact card styling within the existing banner.
- Tests: the edge cases at minimum are a dropped head segment, an added head
  segment, an `@slug` change, a size mismatch, path-only, an ambiguous tail, a
  threshold boundary (2 vs 3), a collision at the new path, a rule with a size
  mismatch (path-only rows withheld), a rule with no size-confirmed rows, an
  idempotent re-apply, and a partial inventory where one library is unreadable
  (no rule may map that library's rows elsewhere). For a mount drop, the test
  must make the NEW paths unreadable too (an empty inventory, and a walk that
  raises), not just the old ones missing, then assert no rule and a refused
  guard. For the dry run, snapshot both tables before and after and assert they
  are identical. For the UI, the relink card renders from the dry run, also
  while the prune is blocked, and after apply the re-fetch drives the banner.
- The inventory must not take minutes on the request: say how it is bounded or
  cached.
