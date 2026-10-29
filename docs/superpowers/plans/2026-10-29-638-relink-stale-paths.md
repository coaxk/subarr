# Relink stale paths before pruning (#638) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Offer one-click path-rewrite rules that move stale audio-lang verifications and probe rows onto the same files at their new paths, so the #453 prune guard only judges what is genuinely gone.

**Architecture:** A pure matcher module (`relink.py`) builds rules from (stale paths, old sizes, verification set, inventory of current files). The inventory comes from a NEW threaded walk of the library roots that records which libraries failed, with a short TTL cache. Two endpoints beside `/admin/db/orphans` expose the dry run (GET) and apply (POST, server recomputes). `OrphanBanner` renders one card per rule above the existing prune text.

**Tech Stack:** Python 3.12, FastAPI, sqlite3 stores, React (vendored) JSX bundled by `scripts/build-frontend.mjs`, vitest + jsdom.

**Spec:** `docs/superpowers/specs/2026-10-29-638-relink-stale-paths-design.md` (read it; every "Settled" item is binding).

## Why the inventory is a walk, not the probe cache

The reporter has 17,052 known paths across both stores and only ~2,000 resolve. If the probe cache held their current files, the union would be ~32k with ~17k present. So the probe cache does NOT contain most current files, and a probe-cache inventory would match almost nothing for the user this fixes. The inventory is a walk of every library root. Put this paragraph in the PR description.

## Global Constraints

- Tail match: >= 3 shared trailing segments, longest shared tail wins; exact, case-sensitive comparison on `/`-split canonical paths; the `@slug` head is never a tail segment; a stale path with < 3 segments (after removing `@slug/`) cannot match.
- Size: when a `media_probe` row exists at the OLD path with a non-None `size`, the current file's size must equal it. `size is None` or no probe row = path-only. `source='arr_mediainfo'` sizes count.
- Filter candidates by size BEFORE the ambiguity test.
- Ambiguity: a tie for the longest shared tail -> unmatched. A current file claimed as best match by more than one stale path -> all of those stale paths unmatched.
- Path-only rows are included in a rule only if the rule has >= 1 size-confirmed match AND 0 size mismatches.
- Rule offered only with >= 3 included pairs whose OLD path has a verification (`RULE_MIN_VERIFIED = 3`). Probe-only stale rows ride along but do not count.
- Rule identity: exact `(old_head, new_head)` strings; heads may be `""`; a non-default head starts with `@slug` (e.g. `"@disk2/Old"`, `"@disk2"`).
- Existing verification at the new path wins: the stale row is not moved (`skipped_collision`). Same NOT EXISTS rule for probe rows.
- **Collisions are excluded at MATCH time, not only at apply** (cold-check finding): a pair is a collision when (old has a verification AND new has a verification) or (old has no verification AND new has a probe row). Collisions are never in `included`, never count toward the threshold, and are reported as `collisions` on the rule. Without this, a rule with >= 3 collided pairs is re-offered forever and every click reports `relinked: 0`.
- Ambiguity (spec "matches more than one current file", read together with "take the longest shared tail"): candidates are ranked by shared-tail length after the size filter; a unique longest wins, a tie is unmatched. Additionally, a current file that is the winner for more than one stale path leaves all of them unmatched (the anti-goal: never move a verification onto a file another verification also claims). This is a stated reading of the spec, not new scope.
- Deterministic order: every pair list sorts by `old` path ascending. `sample` = first 5 of `included` in that order.
- A rule with no size-confirmed match is not offered at all; its rows stay in the ordinary prune list. That is the spec's guardrail, and it is visible as the prune count, not silent data loss.
- Stale set = paths from `audio_lang.all_paths()` ∪ `probe_store.all_paths()` for which `routers/admin.py::_path_exists` is False. Unresolvable paths are PRESENT, hence never stale.
- A row whose OLD library or whose candidate's library had a walk error is never matched.
- No writes in the GET. All stat/walk/SQL work in `asyncio.to_thread`. SQL stays in the stores; no whole-table hydration of probe rows (paths-only `all_paths()` is already used by the prune and is acceptable).
- Logs pass paths through `log_safe.scrub`.
- Provenance on relink: `evidence["relinked_from"]`, `evidence["relinked_at"]`; #563 carry-over keeps `carried_from` / `carried_at` byte-for-byte.
- No force/override on the ratio guard; no change to `MAX_MISSING_RATIO` or `MIN_TOTAL_TO_JUDGE`; no automatic relink.
- Python files: ruff clean. Frontend: commit the rebuilt `review.bundle.js` + `.map` (drift check).

## Review Focus

1. **Mount drop mid-walk** (root lists, then `os.walk` hits an `OSError` deep inside): must mark that library failed, not yield a partial inventory that looks complete. Test in Task 2 (`test_walk_error_marks_library_failed`).
2. **Two old layouts pointing at the same new file** (rows recorded under both `Old1/...` and `Old2/...`): a verification must never be silently merged/overwritten. Test in Task 1 (`test_two_stale_paths_claiming_one_file_are_both_unmatched`).
3. **File changed between GET and POST** (deleted or replaced with a different size): apply must re-stat and skip into `failed`, never move onto a missing or different file. Test in Task 4 (`test_apply_restats_targets_and_skips_changed_files`).
4. **Stale POST** for a rule that is no longer offered (already applied, or files moved): 200 with `applied: false`, nothing written. Test in Task 4 (`test_apply_unknown_rule_writes_nothing`).
5. **Large rule payloads** (15k pairs): GET must cap listed pairs per section (500) and report how many were truncated, so the Review page does not pull megabytes. Test in Task 4 (`test_dry_run_caps_listed_pairs`).

---

### Task 1: Pure matcher `relink.py`

**Files:**
- Create: `src/subarr/relink.py`
- Test: `tests/test_relink_matcher_638.py`

**Interfaces:**
- Produces:
  - `RULE_MIN_VERIFIED: int = 3`, `MIN_TAIL: int = 3`
  - `split_head(canonical: str) -> tuple[str, list[str]]` returns `(slug_prefix, segments)` where `slug_prefix` is `"@slug"` or `""` and segments exclude it.
  - `@dataclass(frozen=True) class Pair: old: str; new: str; how: Literal["size", "path_only"]`
  - `@dataclass class Rule: old_head: str; new_head: str; by_size: list[Pair]; path_only: list[Pair]; path_only_withheld: int; mismatches: int; collisions: int; verified: int` with property `included -> list[Pair]` (= `sorted(by_size + path_only, key=old)`).
  - `build_rules(stale: Iterable[str], *, old_sizes: dict[str, int | None], verified: set[str], probed: set[str], inventory: dict[str, int], failed_slugs: set[str]) -> list[Rule]` returns offered rules only, sorted by `verified` desc then `(old_head, new_head)`. `probed` = every canonical path that has a `media_probe` row (used only for the collision test).
  - A canonical's library slug is `split_head(c)[0]` with the `@` stripped; the default library's slug is `""`. `failed_slugs` uses the same form.
  - Mismatch bookkeeping, exactly: with an int old size, filter candidates to equal size, then rank. If the filter leaves >= 1 candidate, rank those (a shorter-tail size match with a different head is a valid match). If it leaves 0, rank the unfiltered candidates. A UNIQUE best there records one mismatch against that candidate's `(old_head, new_head)`. A tie records nothing. Either way the stale path is unmatched.
  - Heads are joined as `slug_prefix + "/" + "/".join(head_segments)` when both non-empty, else whichever is non-empty, else `""`.

- [ ] **Step 1: Write the failing tests** (each builds a tiny `inventory` dict and calls `build_rules`):
  - `test_dropped_head_segment`: stale `Old/Anime/Movies/A/a.mkv` x3 (distinct files), inventory `Anime/Movies/A/a.mkv`... sizes equal -> one rule `("Old", "")`, `len(by_size) == 3`.
  - `test_added_head_segment`: stale `Movies/X/x.mkv` -> inventory `Anime/Movies/X/x.mkv` -> rule `("", "Anime")`.
  - `test_slug_change`: stale `@disk2/Movies/F/f.mkv` -> inventory `Movies/F/f.mkv` -> rule `("@disk2", "")`; and `split_head("@disk2/Movies/f.mkv") == ("@disk2", ["Movies", "f.mkv"])`.
  - `test_size_mismatch_is_no_match_and_counts_against_rule`: 3 size-confirmed + 1 mismatch under the same heads -> rule has `mismatches == 1`, the mismatched path absent from `included`.
  - `test_path_only_included_when_rule_proven`: 3 sized + 2 with `old_sizes[p] is None` -> `len(path_only) == 2`.
  - `test_path_only_withheld_on_mismatch`: same + 1 mismatch -> `path_only == []`, `path_only_withheld == 2`.
  - `test_rule_with_no_size_confirmed_rows_withholds_path_only`: 4 path-only only -> no rule offered (included verified count 0 < 3).
  - `test_ambiguous_tail_is_unmatched`: inventory has `A/Show/S1/e.mkv` and `B/Show/S1/e.mkv`, stale `Old/Show/S1/e.mkv` -> not in any rule.
  - `test_longer_tail_disambiguates`: inventory `Anime/Show/S1/e.mkv` and `TV/Show/S1/e.mkv`, stale `Old/Anime/Show/S1/e.mkv` -> matches the Anime one.
  - `test_size_filter_runs_before_ambiguity`: two tail candidates, only one with the right size -> matched (not ambiguous).
  - `test_threshold_boundary`: 2 verified pairs -> `[]`; 3 -> one rule. A 4th probe-only pair does not lift 2 to offered.
  - `test_short_paths_never_match`: stale `Show/e.mkv` (2 segments) -> no rule.
  - `test_case_sensitive`: `old/show/s1/e.mkv` vs inventory `Show/S1/e.mkv` -> no match.
  - `test_two_stale_paths_claiming_one_file_are_both_unmatched` (Review Focus 2).
  - `test_failed_library_rows_never_match`: stale `@disk2/...` with `failed_slugs={"disk2"}` -> no rule; and a stale default-library row whose only candidate is in a failed library -> no rule.
  - `test_collisions_excluded_and_counted`: 4 verified stale rows whose new paths are ALL already verified, plus 3 clean -> rule offered with `verified == 3`, `collisions == 4`; with only the 4 collided, no rule. A probe-only stale row whose new path is in `probed` is a collision too.
  - `test_shorter_tail_size_match_wins_over_longer_tail_mismatch`, `test_mismatch_tie_records_nothing`.
  - `test_pairs_sorted_by_old_path`.

- [ ] **Step 2: Run** `PYTHONPATH=src python -m pytest -q tests/test_relink_matcher_638.py` — Expected: FAIL (`ModuleNotFoundError: subarr.relink`).

- [ ] **Step 3: Implement `relink.py`.** Index the inventory by the last `MIN_TAIL` segments (`tuple`) -> list of canonicals. For each stale path: look up candidates by its 3-tail; drop candidates in `failed_slugs`; if the stale's own slug is in `failed_slugs`, skip. Compute the shared tail length per candidate. Size handling: if `old_sizes.get(s)` is an int, size-filter first; if that leaves 0 candidates, and ignoring size there was a unique best candidate, record a mismatch against that candidate's `(old_head, new_head)`. Pick the unique best by tail length (a tie means unmatched). Then drop every new path claimed by more than one stale path. Group by heads, apply the path-only guardrail, count `verified` over `included`, and keep rules with `verified >= RULE_MIN_VERIFIED`. Pure: no I/O, no imports from `config`.

- [ ] **Step 4: Run** the test file — Expected: all PASS. `ruff check src/subarr/relink.py tests/test_relink_matcher_638.py` clean.

- [ ] **Step 5: Commit** `git add src/subarr/relink.py tests/test_relink_matcher_638.py && git commit -m "feat(relink): pure tail+size matcher for stale paths (#638)"`

### Task 2: Inventory walk with failure tracking and TTL cache

**Files:**
- Modify: `src/subarr/relink.py` (append)
- Test: `tests/test_relink_inventory_638.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Inventory: files: dict[str, int]; failed_slugs: frozenset[str]`
  - `walk_inventory() -> Inventory`: for each `settings.libraries` lib, a root that is not a readable dir, or ANY `os.walk` `onerror` callback, adds `lib.slug` to `failed_slugs` and discards that library's files. Files filtered by `paths.VIDEO_EXTS`, keyed by `paths.fs_to_canonical`, size from `os.stat(...).st_size` (stat failure skips just that file).
  - `get_inventory(*, max_age_s: float = 300.0) -> Inventory` caches by `tuple((lib.slug, str(lib.fs_root)) for lib in settings.libraries)` and age. `invalidate_inventory() -> None`. The lock IS held across the walk, so concurrent GETs wait for one walk instead of starting several. `Inventory` also carries `walk_s: float` (walk duration), logged at INFO and returned by the GET. Bound: one `os.stat` per video file, nothing else (no ffprobe, no reads), at most once per 300 s.
  - **Import `settings`, `VIDEO_EXTS` and `fs_to_canonical` INSIDE the functions, never at module level.** The `subarr_env` fixture reloads `config` and `paths` (`tests/conftest.py:369-388`), and a module-level import goes stale under `two_libraries`. `app.py:_walk_all_library_files` imports lazily for the same reason.
  - An EMPTY readable root yields no files and is not failed: the result is no matches, so the guard behaves as today.

- [ ] **Step 1: Write the failing tests** using `subarr_env` + `media_root` (only `subarr_env` points the default library at `media_root`, `tests/conftest.py:349`), and `two_libraries` (`:57`) for disk2:
  - `test_walk_lists_video_files_with_sizes`: write `TV/Show/S1/e.mkv` (5 bytes) -> `files["TV/Show/S1/e.mkv"] == 5`; `.srt` not listed.
  - `test_unreadable_root_marks_library_failed`: delete the disk2 root -> `"disk2" in failed_slugs`, no `@disk2/` keys.
  - `test_walk_error_marks_library_failed` (Review Focus 1): monkeypatch `os.walk` in `subarr.relink` to call `onerror(OSError())` after yielding one dir -> library failed, its files discarded.
  - `test_inventory_is_cached_and_invalidated`: two `get_inventory()` calls walk once (count via monkeypatched `walk_inventory`); after `invalidate_inventory()` it walks again.

- [ ] **Step 2: Run** — Expected: FAIL (`ImportError`).
- [ ] **Step 3: Implement** as specified (module-level cache guarded by a `threading.Lock`).
- [ ] **Step 4: Run** both relink test files — PASS; ruff clean.
- [ ] **Step 5: Commit** `feat(relink): library inventory walk that records failed roots (#638)`

### Task 3: Store moves (`relink` on audio_lang, `move` + `sizes_for` on probe)

**Files:**
- Modify: `src/subarr/audio_lang_store.py:424-453` (`_move_verdict`), add `relink`
- Modify: `src/subarr/probe_store.py` (add `sizes_for`, `move` beside `delete` at :168)
- Test: `tests/test_relink_stores_638.py`; existing `tests/test_verdict_carry_over_563.py`, `tests/test_carry_by_id_571.py` must stay green.

**Interfaces:**
- Produces:
  - `AudioLangStore._move_verdict(self, old: str, new_path: str, *, kind: Literal["carried", "relinked"] = "carried") -> AudioLangVerification | None`: writes `evidence[f"{kind}_from"]` / `evidence[f"{kind}_at"]`. The log line reads `audio-lang verdict carried over (#563): ...` for `carried` (unchanged) and `audio-lang verdict relinked (#638): ...` for `relinked`.
  - `AudioLangStore.relink(self, old: str, new_path: str) -> AudioLangVerification | None` = `_move_verdict(old, new_path, kind="relinked")`.
  - `ProbeStore.sizes_for(self, paths: Iterable[str]) -> dict[str, int | None]`: SQL `IN` in chunks of 500, only rows that exist.
  - `ProbeStore.move(self, old: str, new: str) -> bool`: `UPDATE media_probe SET canonical_path=? WHERE canonical_path=? AND NOT EXISTS (... = new)`. In the same lock it moves the matching `probe_failures` row under the same NOT EXISTS rule. Returns True iff the media_probe row moved.

- [ ] **Step 1: Failing tests:** `test_relink_records_relinked_from_not_carried_from` (asserts `"carried_from" not in evidence`, lang/source/confidence unchanged); `test_relink_refuses_when_target_has_verdict` (returns None, both rows unchanged); `test_carry_over_evidence_unchanged` (`carry_over` still writes `carried_from`); `test_probe_move_and_collision` (moves; second move onto an occupied path returns False, both rows intact); `test_probe_move_carries_failure_row`; `test_sizes_for_returns_only_existing_rows_in_chunks` (1,200 paths seeded, 3 queried + 1 unknown).
- [ ] **Step 2: Run** — FAIL (`AttributeError`).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the new file plus `tests/test_verdict_carry_over_563.py tests/test_carry_by_id_571.py tests/test_orphan_prune.py` — all PASS; ruff clean.
- [ ] **Step 5: Commit** `feat(stores): relink verdicts and move probe rows by path (#638)`

### Task 4: Endpoints

**Files:**
- Modify: `src/subarr/routers/admin.py` (after `db_orphans_prune`, ~line 315)
- Test: `tests/test_relink_api_638.py`

**Interfaces:**
- Consumes: Task 1 `build_rules`/`Rule`/`Pair`, Task 2 `get_inventory`/`invalidate_inventory`, Task 3 store methods, existing `_orphan_stores`, `_path_exists`.
- Produces:
  - `GET /api/admin/db/orphans/relink` -> `{"rules": [RuleJSON], "stale": int, "inventory": {"files": int, "unreadable_libraries": [slug], "walk_s": float}}`.
    `RuleJSON = {"old_head", "new_head", "verified", "by_size": int, "path_only": int, "path_only_withheld", "mismatches", "collisions", "sample": [{"old","new","how"}] (first 5 of included), "pairs_by_size": [...], "pairs_path_only": [...], "truncated": int}`. Each pairs list is capped at `RELINK_PAIRS_CAP = 500`; `truncated` = the total dropped by the caps.
  - `POST /api/admin/db/orphans/relink` body `{"old_head": str, "new_head": str}` -> `{"applied": bool, "reason": str, "relinked": int, "skipped_collision": int, "failed": int}`. Recomputes the rules server side. If the rule is not offered: `applied: false`, nothing written. Each included pair gets EXACTLY ONE outcome, in this order:
    1. Re-stat the target (`canonical_to_fs(new).is_file()`, and for `how == "size"` require `st_size == old size`). On failure: `failed`, and nothing for this pair moves.
    2. If old has a verdict: `audio_lang.relink(old, new)`. None means `skipped_collision` (a verdict appeared at new since the analysis), and the probe row does NOT move either, so the pair stays consistent. On success, also `probe_store.move(old, new)`: a False there is fine (an existing probe row at new is kept, and the old one is pruned later). Outcome `relinked`.
    3. If old has no verdict: `probe_store.move(old, new)` True means `relinked`, False means `skipped_collision`.
    Calls `invalidate_inventory()` after.
  - A private `_relink_analysis(request) -> tuple[list[Rule], int, Inventory]`, run in `asyncio.to_thread`. Stale = the union of the stores' `all_paths()` filtered by `not _path_exists(p)`. `verified = set(audio_lang.all_paths())`. `probed = set(probe_store.all_paths())`. `old_sizes = probe_store.sizes_for(stale)`.
  - Test helper (in the test file): `_seed_probe(store, path, size, source="ffprobe")` calls `store.upsert(canonical_path=path, mtime=0.0, size=size, result=ProbeResult(canonical_path=path), source=source)`. A `size=None` row is made by upserting and then `store._conn.execute("UPDATE media_probe SET size = NULL WHERE canonical_path = ?", (path,))`. Use `source="arr_mediainfo"` in one size-confirmed pair.

- [ ] **Step 1: Failing tests** (use `app_with_stub`, `media_root`, `two_libraries`; seed real files with distinct byte lengths):
  - `test_dry_run_offers_rule_and_writes_nothing`: snapshot `SELECT * FROM audio_lang_verifications` and `SELECT * FROM media_probe` before/after the GET -> identical.
  - `test_apply_relinks_then_guard_judges_remainder`: 4 verified stale under `Old/...` with files at `Anime/...` plus 1 truly gone -> POST relinks 4; then `GET /api/admin/db/orphans` shows `would_delete == 1`, `safe is True`, and the verdict at the new path carries `relinked_from`.
  - `test_apply_is_idempotent`: second POST -> `applied: false` (the rule is no longer offered), counts 0. Run it twice: once with no collisions, once with 3 pre-existing verdicts at new paths alongside 3 clean pairs (still no re-offer after the first apply).
  - `test_collision_existing_verdict_wins`: new path already verified `fr` -> the stale `en` is not moved, the new path stays `fr`, and the stale row is still listed by the orphans GET.
  - `test_mount_drop_offers_nothing_and_guard_refuses`: make the library root unreadable for the walk too (monkeypatch `subarr.relink.walk_inventory` to return `Inventory({}, frozenset({""}))`, and separately the empty-root case) -> `rules == []`, and the orphans GET `safe is False`.
  - `test_partial_inventory_unreadable_library_never_maps_elsewhere` (two_libraries).
  - `test_apply_restats_targets_and_skips_changed_files` (Review Focus 3): GET, then delete one target and rewrite another with a new size, then POST -> `failed == 2`, and those two rows are unmoved.
  - `test_apply_unknown_rule_writes_nothing` (Review Focus 4).
  - `test_dry_run_caps_listed_pairs` (Review Focus 5): monkeypatch `RELINK_PAIRS_CAP = 2` -> `len(pairs_by_size) == 2`, `truncated` correct.
- [ ] **Step 2: Run** — FAIL (404).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/test_relink_*_638.py tests/test_orphan_prune_api.py` — PASS; ruff clean.
- [ ] **Step 5: Commit** `feat(admin): relink dry run and apply endpoints (#638)`

### Task 5: OrphanBanner relink cards

**Files:**
- Modify: `src/subarr/static/v1/home-hifi/review.jsx:772` (`OrphanBanner`)
- Rebuild: `review.bundle.js`, `review.bundle.js.map` via `npm run build:frontend`
- Test: `src/subarr/static/v1/home-hifi/__tests__/review-orphan-relink.test.jsx` (harness pattern: `__tests__/review-bulk-failure-wiring.test.jsx`, the vendored React loader and fetch stubbed at the network edge)

**Interfaces:**
- Consumes: Task 4 GET/POST shapes.
- Behaviour: on mount, fetch `/api/admin/db/orphans` AND `/api/admin/db/orphans/relink` (a relink fetch failure is silent and shows no cards). Render one card per rule ABOVE the existing prune/refusal text, including while the prune is `blocked`. Card copy: `{old_head || '(library root)'} → {new_head || '(library root)'}`, then `{by_size} matched by path + size, {path_only} by path only`. When `path_only_withheld > 0`, add `({path_only_withheld} path-only rows held back: a size did not match)`. Show the 5 `sample` pairs, plus a "Show all" expander listing `pairs_by_size` and `pairs_path_only` in separate sections, with `and {truncated} more` when non-zero. Buttons **Relink all** (POST `{old_head,new_head}`, disabled while busy) and **Skip** (hides that card in component state only). After a POST, re-fetch BOTH GETs and render from them, never from the POST body. A result line shows `Relinked {relinked}` plus non-zero `skipped_collision` / `failed`. It is held in a NEW `relinkDone` state, separate from the prune's `done`, which renders only prune outcomes (`review.jsx:830-835`). Change the early return at `review.jsx:814` to `if (n === 0 && !done && !relinkDone && rules.length === 0) return null;`. Otherwise a relink that clears every stale row would hide its own result line. Dismiss is unchanged.

- [ ] **Step 1: Failing tests:** `renders a relink card from the dry run`; `renders the card while the prune is blocked` (orphans GET `safe:false`, `would_delete: 15000`); `Relink all posts the rule and re-fetches, banner follows the server` (server state flips: after POST the relink GET returns `rules: []` and orphans returns `would_delete: 1, safe: true` -> card gone, "Remove 1 row" offer visible on expand); `Skip hides only that card`; `a failed relink fetch shows no card and the prune banner still works`; `the relink result line survives a relink that clears every stale row` (after the POST, orphans returns `would_delete: 0` and relink returns `rules: []` -> "Relinked 4" still visible).
- [ ] **Step 2: Run** `npx vitest run src/subarr/static/v1/home-hifi/__tests__/review-orphan-relink.test.jsx` — FAIL.
- [ ] **Step 3: Implement** in `OrphanBanner` (a `RelinkCard` sub-component in the same file is fine).
- [ ] **Step 4: Run** the new test file plus `npm run test:frontend` (whole suite) — PASS. Then `npm run build:frontend` and `npm run check:frontend` -> exit 0 after staging the bundle.
- [ ] **Step 5: Commit** `feat(review): relink cards in the orphan banner (#638)` with `review.jsx`, both bundle files and the test.

### Task 6: CHANGELOG and full gate

**Files:** Modify `CHANGELOG.md` (`## [Unreleased]` -> `### Fixed`, a #638 entry in the repo's user-facing voice: what the user saw, what now happens, and the thanks line to @ErroneousBosch).

- [ ] **Step 1:** Add the entry. Run `PYTHONPATH=src python -m pytest -q tests/test_changelog_section.py`, then the full `PYTHONPATH=src python -m pytest -q`, `ruff check src tests`, `npm run test:frontend`, `npm run check:frontend`. All pass. Paste the tail of each run into the PR.
- [ ] **Step 2: Commit** `docs(changelog): relink stale paths (#638)`.
