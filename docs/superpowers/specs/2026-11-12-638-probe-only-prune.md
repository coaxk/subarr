# #638 follow-up 2: cache-only rows can never be pruned

Date: 2026-11-12. Branch `fix/638-probe-only-prune`, worktree `C:\Projects\subarr-638c`.
Base: `3cdbf6c` (main). Earlier specs: `2026-10-29-638-relink-stale-paths-design.md`,
`2026-10-31-638-movie-depth-relink.md`. Revision 2 (after spec-check): witness scope is the
library's top folder, and the two row classes are judged independently.

## Problem (evidence)

The reporter (ErroneousBosch) on 2026-11-11: "So this is still happening, is there any way to just
force the prune?" Their 11-01 `GET /api/admin/db/orphans/relink` output, under two path configs:

```
{"rules":[],"stale":2816,"inventory":{"walked":false,...},"unmatched":null}
{"rules":[],"stale":11062,"inventory":{"walked":false,...},"unmatched":null}
```

`walked:false` comes from `src/subarr/routers/admin.py` `_relink_analysis` (line 353): the walk is
skipped when fewer than `relink.RULE_MIN_VERIFIED` (3) stale paths are in the audio_lang store. So
of 11,062 stale rows at most 2 carry a verification. The rest exist ONLY in the probe cache
(`probe_store`), which is regenerable: a rescan re-probes the file at its new path.

The prune guard (`src/subarr/orphan_prune.py` `prune_missing_multi`) judges ONE ratio over the
union of both stores and refuses everything when more than 50% is missing. Cheap cache rows count
the same as hand-built verifications, so a library with many renames and few verifications is
refused forever. There is no override.

## Design

Two classes of row, each with its own storage-failure test.

- **Protected rows**: paths present in any store NOT named in `regenerable` (in the app:
  audio_lang). A path in both stores is protected. Rule: the existing `prune_decision` ratio,
  judged over the distinct protected paths only. Unsafe -> every missing protected path is HELD.
- **Cache-only rows**: paths present ONLY in stores named in `regenerable` (in the app: probe).
  No ratio. A missing cache-only path is prunable when its SCOPE is live, else HELD.

The classes are judged independently: a held protected set does not hold cache rows, and vice
versa. This replaces the old "one decision for both" rule, which existed because both classes were
judged by the same ratio; each class now has its own storage-failure test, and under a real share
failure both fail together anyway.

### Scope and liveness (cache-only rows)

- The scope of a path is `(library, folder)`: `library` = its slug (`""` = default library 0);
  `folder` = the first segment of the path relative to the library when that relative path has 2
  or more segments, else `""`. Examples: `@anime/Movies/A/a.mkv` -> `("anime", "Movies")`;
  `TV/Show/e.mkv` -> `("", "TV")`; `@x/a.mkv` -> `("x", "")`.
  Why the top folder: the unit of failure is the mount, and a library root like `/Media` commonly
  has a separate share per top folder. Three readable files in `Movies` prove nothing about `TV`.
- `MIN_LIVE_WITNESSES = 3`. A witness is a distinct known path (any store, either class) whose
  state is `"present"`. One cached dentry must not be enough, hence 3.
- A scope is LIVE when:
  1. it has `>= MIN_LIVE_WITNESSES` witnesses, OR
  2. `folder != ""`, the injected `folder_gone(library, folder)` returns True (the folder itself
     does not exist on disk), AND its library has `>= MIN_LIVE_WITNESSES` witnesses across all its
     scopes. Why: an unmounted share still leaves its mountpoint folder in place, so a folder that
     is gone entirely was renamed or deleted, not unmounted; the library-wide witnesses show the
     library's own storage answers.
- Otherwise the scope is DEAD and its missing cache-only paths are held.

### Path state is three-valued

`_path_exists` treats an UNRESOLVABLE path (library removed from config, traversal guard) as
present, deliberately (`admin.py:233-248`, `test_an_unresolvable_path_is_never_pruned`). That is
right for "is it missing" and WRONG for "is the scope live": an unresolvable path proves nothing
was read. So:

- New `PathState = Literal["present", "missing", "unknown"]` in `orphan_prune.py`.
- New `_path_state(canonical) -> PathState` in `routers/admin.py`: `canonical_to_fs` raises ->
  `"unknown"`; `.exists()` True -> `"present"`; False -> `"missing"`; `.exists()` raising ->
  `"unknown"`.
- `_path_exists(canonical)` becomes `return _path_state(canonical) != "missing"`, docstring kept.
  Relink keeps calling `_path_exists`; relink behaviour must not change.
- Only `"present"` is a witness. `"unknown"` is never missing, never held, never a witness, and
  never listed anywhere.

## Changes

### 1. `src/subarr/paths.py`

Add public `prune_scope(canonical: str) -> tuple[str, str]` implementing the scope rule above on
top of `_split_canonical` (`paths.py:42`). One-line docstring plus the three examples. No other
change.

### 2. `src/subarr/orphan_prune.py`

- Add `MIN_LIVE_WITNESSES = 3` and `PathState`, each with a comment giving the reason above.
- New frozen dataclass `HeldScope(library: str, folder: str, missing: int, present: int)`:
  `missing` = held cache-only paths in that scope, `present` = its witness count.
- `MultiPruneReport` gains `held: list[str]` (all held paths, both classes, union order),
  `held_protected: int`, and `held_scopes: list[HeldScope]` sorted by `missing` desc, then
  `library` asc, then `folder` asc. All three have defaults (`field(default_factory=list)` / `0`).
  `total_paths` stays `len(seen)` (distinct union).
- Signature: `prune_missing_multi(stores, *, exists=None, state=None, dry_run=False,
  max_missing_ratio=MAX_MISSING_RATIO, regenerable=frozenset(), scope_of=None, folder_gone=None)`.
  - Neither `exists` nor `state` -> `TypeError`; both -> `ValueError`. `exists` maps True ->
    `"present"`, False -> `"missing"`.
  - `scope_of` defaults to `lambda p: ("", "")`; `folder_gone` defaults to `lambda lib, f: False`.
    The module must NOT import `paths`; the caller injects both.
  - Union and de-duplication as today, first-seen order. Call the state function ONCE per distinct
    path; call `folder_gone` at most once per scope, and only for a scope that fails rule 1.
- Computation:
  - `prot_missing` = missing protected paths. If there are protected paths and
    `prune_decision(total=<distinct protected>, missing=len(prot_missing))` is unsafe, all of
    `prot_missing` is held (`held_protected = len(prot_missing)`), else it is prunable.
  - Missing cache-only paths in a LIVE scope are prunable; in a DEAD scope they are held.
  - `prunable` and `held` are in union order.
- Outcomes:

| Case | `decision.safe` | `would_delete` | report `missing` | deletes |
|---|---|---|---|---|
| union empty | False | 0 | [] | none; existing "nothing to judge" reason |
| nothing missing | True | 0 | [] | none; existing "no missing paths" reason |
| prunable empty, held non-empty | False | len(held) | held | none |
| prunable non-empty | True | len(prunable) | prunable | prunable only (unless dry_run), from every store holding the path |

- Reason = space-joined parts, in this order, empty parts dropped:
  1. When prunable non-empty: `f"{n} of {total} paths are missing ({n/total:.0%}) -- consistent
     with renames or deletions; safe to prune."` with `n = len(prunable)`, `total = len(seen)`.
  2. When `held_protected > 0`: the refusal text of the protected `prune_decision` call, unchanged
     (it contains "storage mount").
  3. When any cache-only path is held: HELD text, `f"{h} cached probe row{'s' if h != 1 else ''}
     {'are' if h != 1 else 'is'} on hold: too few files could be read under {places} to show
     the share is mounted. They are only a cache and will be cleared once those folders are
     readable again."` where `places` names each held scope in `held_scopes` order, at most 5,
     comma-joined, then `f" and {k} more"` if more. A scope is named `@<slug>/<folder>`,
     `@<slug>` (folder ""), `<folder>` (default library), or `the default library` (both "").
- `prune_missing` (single store) and `prune_decision` are NOT changed.
- Module docstring: add one paragraph on the two classes and the scope witness. Do not rewrite the
  existing text, except the `prune_missing_multi` docstring, which must be rewritten to describe
  the independent judgement (its "if either is unsafe to touch, neither is" is no longer true).

### 3. `src/subarr/routers/admin.py`

- Add `_path_state`; rewrite `_path_exists` on top of it.
- Add `_folder_gone(library: str, folder: str) -> bool`: build the canonical (`f"@{library}/{folder}"`,
  or `folder` when library is `""`), `canonical_to_fs`, return `not p.exists()`. Any exception ->
  False (never assume gone).
- `_sweep` calls `prune_missing_multi(stores, state=_path_state, dry_run=dry_run,
  regenerable=frozenset({"probe"}), scope_of=prune_scope, folder_gone=_folder_gone)` (import
  `prune_scope` from `..paths` inside the function, matching the file's local-import style).
- The response gains `"held": len(rep.held)`, `"held_verified": rep.held_protected`, and
  `"held_scopes": [{"library", "folder", "missing", "present"} ...]`, in EVERY response shape,
  including the no-stores early return (`0`, `0`, `[]`). Every existing key keeps its meaning.
  Carry-over is unchanged.

### 4. No frontend change

Do not edit any `.jsx` or `.bundle.js`. Known and accepted: `review.jsx:970-974` shows a fixed
string for `safe:true` and never `reason`, so held rows are not mentioned until the NEXT load,
where `safe:false` + `would_delete > 0` renders "cleanup is on hold" with `reason`
(`review.jsx:969`). `review.jsx:1001`'s "deletes saved audio-language verifications" copy is
already shown for probe rows today; not changed here.

## Edge cases (each needs a test)

Unit tests go in the NEW file `tests/test_orphan_prune_classes.py`, with its own copy of `_MultiFakeStore` from `tests/test_orphan_prune.py:178-193` (do not import it). Convention for
these tests: store `"a"` is protected, store `"b"` is regenerable (`regenerable=frozenset({"b"})`),
`scope_of` = a test helper with the same rule as `prune_scope` (write it inline in the test file;
do not import `subarr.paths`), `state=` a dict lookup.

1. Reporter's shape: scope `("", "TV")` with 3 present + 10 missing in "b" only, "a" empty ->
   safe, would_delete 10, those 10 deleted from "b", held [].
2. Same, plus "a" holds 2 present paths in that scope -> same outcome.
3. Share down, cache only: every "b" path missing -> safe False, held = all, nothing deleted,
   `held_scopes == [HeldScope("", "TV", n, 0)]`, reason contains "on hold".
4. Share down with protected rows: everything missing in both -> safe False, nothing deleted from
   EITHER store, `held_protected` = protected count, reason contains "storage mount" AND "on hold".
5. Two scopes: `("", "Movies")` 3 present + 2 missing cache-only; `("", "TV")` 0 present + 4
   missing cache-only, `folder_gone` False -> safe True, prunable = the 2, held = the 4,
   `held_scopes` = TV only, reason contains `TV`.
6. Witness boundary: a scope with exactly 2 present -> its missing cache-only rows held; with 3 ->
   prunable. Must go red if the constant becomes 2 or 4.
7. Witnesses that are all protected rows count: scope with 3 present paths only in "a" + missing
   cache-only -> prunable.
8. Unknown is not a witness: scope with 3 "unknown" + 1 missing cache-only -> held.
9. Unknown is never missing: an "unknown" path is not in `missing` or `held` and is not deleted.
10. Folder renamed away: `("", "Old")` 0 present + 3 missing cache-only, `folder_gone("", "Old")`
    True, library "" has 3 present elsewhere -> prunable.
11. Folder gone but library has only 2 witnesses -> held.
12. Folder present (`folder_gone` False) with 0 witnesses -> held even though the library has 10.
13. `folder_gone` is not called for a scope with >= 3 witnesses, nor for folder "" (count calls).
14. Protected denominator: "a" 4 paths (1 missing); "b" 10 missing + 3 present in one live scope ->
    safe, 11 prunable. In the same test assert `prune_decision(total=17, missing=11).safe is
    False`, proving the old union rule would have refused.
15. Independence: "a" 2 paths both missing (protected ratio unsafe); "b" 5 missing in a live scope
    (3 present in "b") -> safe True, prunable = the 5 cache rows, `held_protected == 2`, the 2
    protected paths NOT deleted, reason contains "storage mount".
16. A path in both "a" and "b" is protected: never held by scope, deleted from both when prunable.
17. `regenerable` empty: the SIX existing `prune_missing_multi` tests (lines 209-278) pass with
    no edit.
18. Neither `exists` nor `state` -> TypeError; both -> ValueError.
19. State function called once per distinct path (a path in two stores).
20. Dry run: prunable and held reported, nothing deleted, `deleted_by_store == {}`.
21. HELD text: "1 cached probe row is on hold", plural, each of the four scope-name forms, and with
    6 held scopes it contains `and 1 more`.
22. `held_scopes` ordering: missing desc, library asc, folder asc.

`prune_scope` unit tests (in the NEW file `tests/test_prune_scope.py`): the three examples above, `@x` (library root, -> `("x", "")`), `""`.

API tests in the NEW file `tests/test_orphan_prune_held_api.py` (fixtures `app_with_stub` + `media_root`; probe rows
via `app.state.probe_store.upsert`, read its signature at `src/subarr/probe_store.py:86`; for
case 27 use the `two_libraries` fixture in `tests/conftest.py` if it composes with
`app_with_stub`, else build both scopes as two top folders of `media_root`, which is what the
design keys on anyway):

23. Reporter's shape end to end: 3 present files under `TV/` with probe rows, 5 missing probe-only
    rows under `TV/`, no verdicts -> GET safe True, would_delete 5, held 0; POST deletes those 5
    probe rows and leaves the 3.
24. Probe-only missing rows under a top folder that EXISTS on disk with no readable file in it ->
    GET safe False, `held` == count, `held_scopes[0]["present"] == 0`; POST deletes nothing.
25. Same rows under a top folder that does NOT exist on disk, with 3 present files elsewhere in the
    library -> POST deletes them.
26. POST after a partial hold, then GET: safe False, would_delete == held count.
27. Two scopes, one live one dead -> POST deletes only the live scope's rows.
28. Every response carries `held`, `held_verified` and `held_scopes`, including the empty-store
    case and the existing mass-disappearance refusal.
29. `_path_state`: present file, missing file, unresolvable library -> "unknown", and a patched
    `.exists()` that raises -> "unknown".
30. `_folder_gone`: missing folder True, existing folder False, unresolvable library False.
31. The existing API tests pass with no edit.

Standing checklist (limits at equality, every error branch, state after failure):

32. Protected ratio AT 50%: "a" 4 paths, 2 missing -> those 2 prunable (`prune_decision` allows
    exactly 50%); 4 paths, 3 missing -> held.
33. Exactly 5 held scopes -> no `and ... more`; 6 -> `and 1 more` (case 21).
34. A `state` function that raises mid-sweep -> the exception propagates and NOTHING is deleted
    from any store (all deletes happen after the whole computation).
35. `_path_state` with `canonical_to_fs` raising `PathOutsideRootError` (traversal `../x.mkv`) ->
    "unknown"; `_folder_gone` with `.exists()` raising `OSError` -> False.
36. State after a hold: a POST that holds everything changes nothing, and the next GET returns the
    identical `held`, `held_scopes` and `would_delete`.
37. State after a partial prune: once the dead scope's files are created on disk (share back),
    the next POST deletes nothing from that scope (its rows are now present), proving held rows
    were kept, not dropped.

## Verification

- `pytest tests/test_orphan_prune.py tests/test_orphan_prune_api.py tests/test_verdict_carry_over_563.py tests/test_orphan_prune_classes.py tests/test_orphan_prune_held_api.py tests/test_prune_scope.py -q`
- The existing `tests/test_orphan_prune.py` and `tests/test_orphan_prune_api.py` are NOT edited.
- `pytest tests -q -k "relink or orphan"` (relink unchanged)
- `ruff check` and `ruff format --check` on every changed file.
- Mutations the reviewer will run: `MIN_LIVE_WITNESSES` 2 and 4 (case 6 red); "unknown" counted
  as a witness (case 8 red); union denominator for protected (case 14 red); re-couple the classes
  so an unsafe protected ratio holds cache rows (case 15 red); drop the folder-gone fallback (case
  10 red); drop the library-witness condition from it (case 11 red); delete held rows (cases 3/24
  red).

## Out of scope

- No force/override flag: the reporter's rows are cache rows and this rule clears them.
- Relink, the `walked:false` short-circuit, and the UI are unchanged.
- The reporter's path-mapping question (`default` = `/Media` vs blank) is separate; it gets a
  reply on the issue, not code.
