# #638 follow-up: relink misses movie libraries, and says nothing about why

Date: 2026-10-31. Branch `fix/638-movie-depth-relink`, worktree `C:\Projects\subarr-638b`.
Base: `b6cca6c` (main). Original design: `docs/superpowers/specs/2026-10-29-638-relink-stale-paths-design.md`.

## Problem (evidence)

The reporter (ErroneousBosch) ran the #640 relink on `:main`: about 3,925 rows relinked, then
`GET /api/admin/db/orphans/relink` returned `"rules": []`, `"stale": 11083`, no unreadable
libraries, 27,154 files walked. The three stale rows they had shared earlier,
`Anime/Movies/Appleseed (1988)/Appleseed (1988) Bluray-1080p.mkv` and two more like it, were not
relinked, although the files exist in the container under `/Media/Anime/...`.

Root cause, reproduced on `b6cca6c`: `build_rules` indexes the inventory by the last `MIN_TAIL = 3`
segments (after removing the `@slug` head) and skips any inventory file with fewer. A library
rooted AT a movies folder (`/Media/Anime/Movies`) gives every movie a 2-segment canonical,
`@slug/Appleseed (1988)/Appleseed (1988) Bluray-1080p.mkv`, so no movie in it can ever be a
candidate. Repro: stale `Anime/Movies/Appleseed {y}/f.mkv` x3, inventory the same tails under
`@animemovies/` (2 segments): `[]`. Under `@anime/Movies/` (3 segments): one rule, 3 by size.

Second problem: when nothing is offered, the API gives no reason, so a remote report cannot tell
this bug from any other gate (size changed, tie, contested, collision, withheld, below threshold).
The reporter's library roots are NOT yet confirmed (asked on the issue), so the breakdown below is
required, not optional: it is how the next report settles whether anything else is going on.

## Change 1: a 2-segment tail matches when, and only when, the size matches

In `src/subarr/relink.py`:

- Keep `MIN_TAIL = 3`, meaning: the shortest shared tail that may match on path alone.
- Add `MIN_TAIL_SIZED = 2`: the shortest shared tail that may match when the stale path's recorded
  size equals the candidate's size.
- Index the inventory by the last `MIN_TAIL_SIZED` segments (any inventory file with >= 2 segments
  after its slug is indexed). Stale paths with fewer than 2 segments after the slug cannot match.
- Candidates for stale S = inventory entries sharing S's last 2 segments, minus S itself, minus
  failed slugs (unchanged). Rank each by shared tail length.
- Size known (`isinstance(old_sizes.get(S), int)`):
  - size pool = candidates whose inventory size equals it (any tail >= 2). If non-empty: unique
    longest tail in the size pool wins, `how="size"`; a tie is unmatched (reason `ambiguous`).
  - If the size pool is empty: no match. Record a mismatch against a rule ONLY when, among
    candidates with tail >= 3, there is a unique longest (exactly today's mismatch logic, restricted
    to tail >= 3). A 2-tail candidate of a different size is not evidence of the same file and
    must not count against a rule.
- Size unknown: pool = candidates with tail >= 3 only (today's behaviour). Empty pool: no match.
  Unique longest wins, `how="path_only"`; a tie is unmatched.
- Everything after matching is unchanged: contested claims, grouping by heads, collisions, the
  path-only guardrail, `RULE_MIN_VERIFIED`, sort order.

Why size and not more segments: `Season 1/E01.mkv` is a 2-segment tail shared by every show, so a
2-segment match on path alone could pair the wrong files. An exact byte size on top of the same
folder and file name is not a coincidence in practice, and the rule still needs
`RULE_MIN_VERIFIED` verified pairs plus the user's click on a visible sample.

## Change 2: a per-row breakdown of why stale rows are not in an offered rule

Add to `relink.py` a function that returns the rules AND a breakdown; keep `build_rules` as a thin
wrapper returning only the rules, so its existing callers and tests keep their meaning. Suggested
shape (names are the builder's choice if consistent):

```python
@dataclass
class Analysis:
    rules: list[Rule]
    reasons: dict[str, int]          # every key below always present, zero allowed
    examples: dict[str, list[str]]   # up to 3 stale paths per reason, sorted, only for count > 0

def analyse(stale, *, old_sizes, verified, probed, inventory, failed_slugs) -> Analysis
```

Every distinct stale path is counted under EXACTLY ONE reason, the first that applies in this
order, so `sum(reasons.values()) == len(set(stale))`:

| reason | meaning |
|---|---|
| `failed_library` | the stale path's own slug is in `failed_slugs` |
| `too_short` | fewer than 2 segments after the slug |
| `no_candidate` | no inventory file shares its last 2 segments |
| `needs_size` | size unknown, and no candidate shares >= 3 segments (only 2-segment candidates exist) |
| `size_changed` | size known, and no candidate has that size |
| `ambiguous` | a tie for the longest tail in the pool |
| `contested` | its winner is also the winner for another stale path |
| `collision` | the new path already has a row (today's `collided` test) |
| `path_only_withheld` | a path-only pair removed by the guardrail |
| `below_threshold` | its pair was in a rule with fewer than `RULE_MIN_VERIFIED` verified pairs |
| `offered` | its pair is in an offered rule |

Notes: `offered` counts included pairs of offered rules. A collided pair in an offered rule is
`collision`, not `offered`. A withheld path-only pair is `path_only_withheld` even if its rule is
also below threshold (guardrail is applied first, today and here).

## Change 3: the API returns the breakdown, including on the cached path

`src/subarr/routers/admin.py`:

- `_relink_analysis` uses `analyse`. `GET /api/admin/db/orphans/relink` gains a top-level
  `"unmatched": {"reasons": {...}, "examples": {...}}`.
- Walk skipped (fewer than `RULE_MIN_VERIFIED` stale verdicts): `"unmatched": null`.
- #641 no-rules cache: `remember_no_rules` must store the breakdown with the inventory, and a cache
  hit must return that same breakdown, so a cached answer and a fresh one are indistinguishable in
  this field. Keep the existing guarantees in `test_relink_no_rules_cache_641.py` (never remember a
  walk with failed libraries; apply never reads the cache).
- `POST` (apply) behaviour and response unchanged. Apply re-runs the analysis fresh, so it inherits
  Change 1 automatically: verify by test that a 2-segment sized rule can be APPLIED end to end.
- No frontend change in this PR.

## Edge cases (each needs a test; list any more you find before writing code)

1. Movie library: stale `Anime/Movies/<T>/<f>` x3 with sizes, inventory `@m/<T>/<f>` same sizes ->
   one rule `("Anime/Movies", "@m")`, 3 by size, reasons `offered: 3`.
2. Same, but stale sizes unknown -> no rule, reasons `needs_size: 3`.
3. Same, sizes known but all different -> no rule, `size_changed: 3`, AND `mismatches` not recorded
   (assert via a mixed case: a rule offered from other rows with the same heads has `mismatches == 0`
   and keeps its path-only pairs).
4. 3-segment wrong-size candidate plus a 2-segment right-size candidate: the size match wins
   (`how="size"`), no mismatch recorded.
5. 2-segment size tie between two current files (same tail, same size) -> `ambiguous`.
6. Generic TV tail: stale `Old/ShowA/Season 1/E01.mkv` (size known) where the only same-size file is
   `ShowB/Season 1/E01.mkv`: shared tail is 2, sizes equal -> this DOES match by the new rule; and if
   `ShowA/Season 1/E01.mkv` also exists at the same size, the 3-tail wins. Write both, so the
   behaviour is pinned and visible to the reviewer.
7. Every existing test in `tests/test_relink_matcher_638.py` that encodes "2 segments never match"
   (`test_short_paths_never_match`) is rewritten to the new contract: path-only 2-segment never
   matches; sized 2-segment does; 1-segment never does. Do NOT delete coverage: each old assertion
   either keeps its meaning or is replaced by its sized/unsized counterpart.
8. Breakdown: one test per reason, plus one invariant test that the reason counts sum to the number
   of distinct stale paths on a mixed fixture that hits every reason.
9. Examples: capped at 3 per reason, sorted, absent for zero-count reasons.
10. API: dry run returns `unmatched`; walk-skipped returns `unmatched: null`; a cache hit returns the
    same `unmatched` as the fresh run that populated it.
11. `test_constants` asserts both constants.

## Files in scope

- `src/subarr/relink.py`, `src/subarr/routers/admin.py`
- `tests/test_relink_matcher_638.py`, `tests/test_relink_api_638.py`,
  `tests/test_relink_no_rules_cache_641.py` (new test files allowed, named `test_relink_*`)
- `CHANGELOG.md`: amend the existing `[Unreleased]` #638 entry (do not add a new #638 bullet). It
  currently says nothing about depth; add one or two plain sentences: a movie library rooted at its
  movies folder is now matched when sizes match, and the relink check reports why remaining rows
  did not match. Same voice as the surrounding entries.

## Verification

- TDD: failing tests first, then implementation. Run `tests/test_relink_*` and the full suite
  (see the repo CLAUDE.md for the command, and the pytest `pythonpath` note: run from THIS worktree
  so it tests this worktree's `src`).
- `ruff check` and `ruff format --check` on changed files.
- Rerun the repro above against the new code and paste its output.

## Decisions after spec-check (these override anything above that reads differently)

1. **Cache API.** `remember_no_rules(fingerprint, inv, analysis=None)` gains an optional third
   argument and stores it. `cached_no_rules(fingerprint)` keeps returning `Inventory | None`
   unchanged (the #641 tests keep their meaning). Add
   `cached_no_rules_analysis(fingerprint) -> tuple[Inventory, Analysis | None] | None`, read under
   the same lock from the same stored entry, so inventory and breakdown can never come from two
   different remembers. `_relink_analysis` returns a 5-tuple
   `(rules, stale_count, inv, cached, analysis_or_None)`; update both callers (`_relink_dry_run`,
   `_relink_apply`).
2. **`no_candidate`** uses the same candidate set as matching: excluding S itself and failed-slug
   entries.
3. **Order is pipeline order.** A contested pair never reaches grouping, so it is `contested` and
   never `collision`, `path_only_withheld` or `below_threshold`. A withheld path-only pair is
   `path_only_withheld` whether or not its rule is also below threshold.
4. **Empty `old_head`** (e.g. stale `Show/e.mkv` sized, matching `@m/Show/e.mkv`) is a valid rule,
   as an empty head already is today for a whole-path tail (`test_dropped_whole_head_gives_empty_new_head`).
   Apply selects the rule by `(old_head, new_head)` and moves its pairs, so no string rewriting
   is involved. Cover it with one apply test.
5. **Examples:** sort the reason's paths, take the first 3.
6. **Walk skipped:** `stale` unchanged, `unmatched: null`.
7. **Performance.** A 2-segment index makes buckets larger (`Season 1/E01.mkv`). Carry each
   candidate's segments in the ranked tuples rather than re-scanning `cands` with `next(...)`, and
   add a timing guard test: 30,000 inventory files where 2,000 share one 2-segment tail, plus
   11,000 stale paths of which 2,000 hit that bucket, must finish `analyse` in under 5 s on the
   test machine. Record the measured time in the report.
8. **Generic 2-segment tails (accepted risk, stated).** A 2-segment match needs an EXACT byte size,
   a unique longest tail within the size pool, no other stale path claiming the same file, and a
   rule (same heads) with at least 3 verified pairs, then the user's click. Different video files
   with identical byte sizes at the same folder/file name are not a realistic population; the
   3-segment size path already rests on the same assumption. No extra generic-name heuristic.
