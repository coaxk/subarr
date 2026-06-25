# #161 Phase 2 — Coverage merge across instances — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `build_coverage` fan out across all Sonarr/Radarr/Bazarr instances and merge their data into one coverage view — rows attributed to their library, byte-identical for single-instance installs.

**Architecture:** Approach A — per-instance assembly + merge (design: `docs/superpowers/specs/2026-06-26-161-phase2-coverage-merge-design.md`). The big-function refactor is gated by a **characterization test** (single-instance byte-identical) written FIRST as the safety net. Small additive pieces (row `library` label, per-instance health) land as independent TDD tasks before the restructure.

**Tech Stack:** Python 3.11+, pytest, asyncio. Worktree: `C:\Projects\subarr-161-p2` (branch `feat/161-phase2-coverage-merge`). The worktree's `subarr` is editable-installed; run `python -m pytest …` (verify `python -c "import subarr,os;print(os.path.dirname(subarr.__file__))"` → worktree `src`; if not, `pip install -e .` in the worktree).

**Critical gotchas (carry into every task):** repo `.py` are CRLF (LF git warnings benign); a **blocking** PostToolUse ruff hook flags F821/F841 on partial edits — add import + usage together, or usages first then import; run `ruff format` on heredoc-appended tests; `build_coverage` is the event-loop-sensitive hot path (#308) — preserve the existing `asyncio.to_thread` offloads and bounded fan-out.

---

## File structure

- **Modify:** `src/subarr/coverage_engine.py` — `CoverageItem.to_dict` (+`library` field); `build_coverage` (the orchestrator restructure); `_fetch_bazarr` → add `_fetch_bazarr_all`; the assembly helpers (`_add_bazarr_blind_synthetic_rows`, `_add_radarr_blind_movie_rows`, inline passes) parameterised to take explicit instance clients instead of `bundle.sonarr/.radarr/.bazarr`.
- **Modify:** `src/subarr/paths.py` — only if a `library_name_for_canonical` helper is wanted (Task 2 decides).
- **Test (new):** `tests/test_coverage_characterization.py` (safety net), `tests/test_coverage_row_library_label.py`, `tests/test_coverage_multi_instance.py`, `tests/test_coverage_sources_per_instance.py`.
- **Modify:** `tests/conftest.py` — extend the `anime_stack` fixture (or add `anime_stack_full`) to seed two Sonarr + two Radarr + two Bazarr + the four bound libraries mirroring KRDucky's topology, with mock-transport wanted lists.

---

## Task 1: Characterization safety net (single-instance byte-identical)

**The most important task — locks the invariant before any refactor.**

**Files:** Test: `tests/test_coverage_characterization.py`

- [ ] **Step 1: Write the characterization test** — drive `build_coverage` with the existing single-instance `app_with_stub`/`subarr_env` fixtures + a deterministic seeded Bazarr/Sonarr/Radarr mock dataset (reuse the handlers in `conftest.py`'s `_make_integration_bundle`). Snapshot the full sorted `CoverageReport.to_dict()` to a committed golden JSON; assert equality.

```python
# tests/test_coverage_characterization.py
"""#161 Phase 2 safety net: build_coverage output for a single-instance stack
must stay byte-identical across the multi-instance refactor."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "data" / "coverage_single_instance_golden.json"


@pytest.mark.asyncio
async def test_build_coverage_single_instance_byte_identical(coverage_fixture):
    # coverage_fixture: a conftest fixture that builds a deterministic bundle
    # (seeded bazarr/sonarr/radarr mock handlers) + probe_store, runs
    # build_coverage, and returns report.to_dict(). See conftest addition below.
    report = await coverage_fixture()
    actual = json.dumps(report, sort_keys=True, indent=2)
    if not GOLDEN.exists():  # first run records the golden; review the diff before committing
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(actual, encoding="utf-8")
        pytest.skip("golden recorded - re-run to assert")
    assert actual == GOLDEN.read_text(encoding="utf-8")
```

- [ ] **Step 2: Add the `coverage_fixture` to conftest** — a deterministic single-instance `build_coverage` runner (seeded bazarr eps/movies, sonarr series, radarr movies, an in-memory probe_store). Mirror the existing `_make_integration_bundle` handler style; keep the dataset small but exercising episode + movie + foreign-blind + has-sidecar paths.

- [ ] **Step 3: Record + eyeball the golden** — run once, inspect `coverage_single_instance_golden.json` for sanity (real titles, scores, canonicals), commit it.

- [ ] **Step 4: Run green** — `python -m pytest tests/test_coverage_characterization.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_coverage_characterization.py tests/data/coverage_single_instance_golden.json tests/conftest.py
git commit -m "test(#161): coverage characterization safety net (phase 2)"
```

**This test MUST stay green through every subsequent task.** A diff = a single-instance regression; stop and fix before proceeding.

---

## Task 2: Emit `library` label on `CoverageItem.to_dict`

**Files:** Modify `src/subarr/coverage_engine.py` (`CoverageItem.to_dict`, ~line 155). Test: `tests/test_coverage_row_library_label.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_coverage_row_library_label.py
from __future__ import annotations


def test_to_dict_includes_library_label(anime_stack):
    from subarr.coverage_engine import CoverageItem

    # @anime/ canonical resolves to the 'anime' library
    item = CoverageItem(media_type="episode", title="Naruto",
                        file_canonical_path="@anime/Naruto/S01E01.mkv")
    d = item.to_dict()
    assert d["library"] == {"slug": "anime", "name": "Anime"}


def test_to_dict_library_default_lib_blank_slug(subarr_env):
    from subarr.coverage_engine import CoverageItem

    item = CoverageItem(media_type="episode", title="Show",
                        file_canonical_path="Show/S01E01.mkv")  # library 0
    d = item.to_dict()
    assert d["library"]["slug"] == ""
```

- [ ] **Step 2: Run → FAIL** (`KeyError: 'library'`). `python -m pytest tests/test_coverage_row_library_label.py -v`

- [ ] **Step 3: Implement** — in `CoverageItem.to_dict`, derive the library from the row's canonical (prefer `file_canonical_path`, else `canonical_path`) via `library_for_canonical` (already imported at `coverage_engine.py:28`), emit `{"slug": lib.slug, "name": lib.name}`. Fail-soft to `{"slug":"","name":...}` for library 0. Add to the returned dict.

- [ ] **Step 4: Run → PASS** + characterization still green (`tests/test_coverage_characterization.py` — golden must be re-recorded ONCE to include the new `library` key; eyeball the diff = only `library` added per row, nothing else changed, then re-commit the golden).

- [ ] **Step 5: Commit** `git commit -m "feat(#161): emit library label on coverage rows (phase 2)"`

---

## Task 3: Per-instance `sources` health structure

**Files:** Modify `src/subarr/coverage_engine.py` (`_fetch_bazarr`/`_fetch_arr` health writes). Test: `tests/test_coverage_sources_per_instance.py`

- [ ] **Step 1: Failing test** — assert that with two Bazarr instances (one ok, one erroring) `sources["bazarr"]["instances"]` is a list of `{id, ok, configured, ...}` and the top-level rollup `sources["bazarr"]["ok"]` is False (an instance is down).

```python
# tests/test_coverage_sources_per_instance.py
from __future__ import annotations


def test_bazarr_sources_per_instance(anime_stack_degraded):
    # anime_stack_degraded: bazarr-anime handler raises; bazarr#0 ok
    sources = anime_stack_degraded["sources"]
    insts = sources["bazarr"]["instances"]
    assert {i["id"] for i in insts} == {"", "anime"}
    assert sources["bazarr"]["ok"] is False  # rollup: an instance is down
    assert any(i["ok"] is False and i["id"] == "anime" for i in insts)
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** — `_fetch_bazarr_all(bundle, sources)` iterates `bundle._clients["bazarr"]`, calls `_fetch_bazarr` per instance writing per-instance status into `sources["bazarr"]["instances"]`, sets the rollup `ok = all configured instances ok`. Keep the legacy top-level keys (`ok`, `episodes_wanted`, `movies_wanted` as summed rollups) so #286's banner + existing consumers keep working. Single-instance: list of one, rollup identical to today.

- [ ] **Step 4: Run → PASS** + characterization green (single-instance rollup unchanged).

- [ ] **Step 5: Commit** `git commit -m "feat(#161): per-instance sources health (phase 2)"`

---

## Task 4: `anime_stack_full` fixture (KRDucky topology)

**Files:** Modify `tests/conftest.py`

- [ ] **Step 1: Add the fixture** — two Sonarr (`""`=TV `/data/tv`, `anime` `/data/anime`), two Radarr (`""`=movies `/data/movies`, `anime` `/data/animefilms`), two Bazarr (`""` fronts tv+movies, `anime` fronts anime+animefilms), four bound libraries. Each integration gets a mock-transport handler returning a small deterministic wanted/series/movies dataset where **the same raw episode id (e.g. 101) appears in both Sonarr instances under different paths** (the collision the refactor must survive). Mirror `two_libraries`/`anime_stack` reload pattern (write override store, reload config/paths/coverage_engine).

- [ ] **Step 2: Sanity** — `python -m pytest tests/test_instances.py -q` (conftest imports clean).

- [ ] **Step 3: Commit** `git commit -m "test(#161): anime_stack_full fixture (2x sonarr/radarr/bazarr, KRDucky topology)"`

---

## Task 5: `_fetch_bazarr_all` + path→library tagging (seam 1)

**Files:** Modify `src/subarr/coverage_engine.py`. Test: extend `tests/test_coverage_multi_instance.py`

- [ ] **Step 1: Failing test** — with `anime_stack_full`, `_fetch_bazarr_all` returns merged episode+movie wanted items, each tagged with its resolved library slug; bazarr-anime's `/data/anime/...` items tag `anime`, its `/data/animefilms/...` items tag `animefilms`.

```python
def test_fetch_bazarr_all_tags_by_library(anime_stack_full):
    import asyncio
    from subarr.coverage_engine import IntegrationBundle, _fetch_bazarr_all

    bundle = IntegrationBundle()
    sources = {}
    eps, movs = asyncio.run(_fetch_bazarr_all(bundle, sources))
    # each item carries a resolved _library_slug from its path
    anime_eps = [e for e in eps if e.get("_library_slug") == "anime"]
    animefilm_movs = [m for m in movs if m.get("_library_slug") == "animefilms"]
    assert anime_eps and animefilm_movs
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** — `_fetch_bazarr_all(bundle, sources)`: for each bazarr instance, fetch `episodes_wanted()`/`movies_wanted()`, and for each returned item resolve its path via `strip_arr_prefix` → `library_for_canonical` and attach `item["_library_slug"]`. Return merged `(eps, movs)` lists. (Reuses the per-instance health from Task 3 — fold them together.) Items whose path matches no library keep `_library_slug=""` (library 0) — degraded but not dropped.

- [ ] **Step 4: Run → PASS** + characterization green.

- [ ] **Step 5: Commit** `git commit -m "feat(#161): _fetch_bazarr_all + path->library tagging (phase 2 seam 1)"`

---

## Task 6: Parameterise the assembly to take explicit instance clients

**Refactor prep — no behaviour change. Characterization is the gate.**

**Files:** Modify `src/subarr/coverage_engine.py`

- [ ] **Step 1: Transform** — change the inline episode/movie passes and the helpers `_add_bazarr_blind_synthetic_rows`, `_add_radarr_blind_movie_rows` so every `bundle.sonarr` / `bundle.radarr` / `bundle.bazarr` access becomes a passed-in client parameter (`sonarr_client`, `radarr_client`). `bundle.tautulli`/`bundle.plex` stay as bundle (singleton). The episode-id-keyed dicts (`sonarr_eps_by_id`, `ep_file_paths`, `eps_by_series_id`, `seen_ep_ids`) become locals of the per-instance assembly scope (Task 7 formalises). This task keeps `build_coverage` calling them ONCE with instance-0 clients → behaviour identical.

- [ ] **Step 2: Run characterization → PASS** (byte-identical) + full coverage tests `python -m pytest tests/ -k coverage -q`.

- [ ] **Step 3: Commit** `git commit -m "refactor(#161): parameterise coverage assembly by instance client (phase 2)"`

---

## Task 7: Orchestrator loop — per-instance assembly + merge (the core)

**Files:** Modify `src/subarr/coverage_engine.py` (`build_coverage`). Test: `tests/test_coverage_multi_instance.py`

- [ ] **Step 1: Failing test** — with `anime_stack_full`, `build_coverage` returns a report where episode 101 (present in BOTH Sonarrs) yields **two distinct rows** with `@`(tv) and `@anime` canonicals (no collision/overwrite), and rows carry the correct `library` label; per-stack Bazarr wanted items land on the right library's rows.

```python
def test_build_coverage_no_cross_instance_collision(anime_stack_full):
    import asyncio
    from subarr.coverage_engine import IntegrationBundle, build_coverage

    report = asyncio.run(build_coverage(IntegrationBundle(), use_tautulli=False))
    rows = report.to_dict()["items"]
    libs = sorted({r["library"]["slug"] for r in rows})
    assert "anime" in libs and "" in libs  # both stacks represented
    # the shared raw ep id did not collapse two instances into one row
    naruto = [r for r in rows if r["library"]["slug"] == "anime"]
    tv = [r for r in rows if r["library"]["slug"] == ""]
    assert naruto and tv
```

- [ ] **Step 2: Run → FAIL** (single global pass collapses the collision).

- [ ] **Step 3: Implement** — restructure `build_coverage`: (a) `_fetch_bazarr_all` once for all bazarr items (tagged by library); (b) group instances → for each Sonarr instance, run the episode assembly + `_add_bazarr_blind_synthetic_rows` with that Sonarr's client + the episode-wanted items whose `_library_slug` binds to that Sonarr (via `settings.libraries` bindings); (c) symmetric per Radarr instance for movies; (d) concatenate all item lists; (e) the shared singleton context (probe index, tautulli activity, plex hints, verifications) is built once and passed in. Bounded concurrency with the existing semaphore pattern. Single-instance: the loop runs once over instance 0 → characterization byte-identical.

- [ ] **Step 4: Run → PASS** (multi-instance) + characterization → PASS (single-instance byte-identical). This is the critical dual-gate.

- [ ] **Step 5: Commit** `git commit -m "feat(#161): per-instance coverage assembly + merge (phase 2 core)"`

---

## Task 8: Dedup re-key (seam 4)

**Files:** Modify `src/subarr/coverage_engine.py`. Test: extend `tests/test_coverage_multi_instance.py`

- [ ] **Step 1: Failing test** — same raw `bazarr_episode_id` across two Sonarr instances must NOT cross-suppress: both rows survive (they have distinct `@slug` canonicals).

```python
def test_dedup_does_not_cross_suppress_instances(anime_stack_full):
    import asyncio
    from subarr.coverage_engine import IntegrationBundle, build_coverage

    report = asyncio.run(build_coverage(IntegrationBundle(), use_tautulli=False))
    canons = [r.get("file_canonical_path") for r in report.to_dict()["items"]]
    # the duplicated raw id produced two distinct library-tagged canonicals
    assert len({c for c in canons if c}) == len([c for c in canons if c])
```

- [ ] **Step 2: Run → FAIL or PASS** — if Task 7's per-instance scoping already isolates `seen_ep_ids` (it should), this passes; the test then *guards* it. If it fails, scope `seen_ep_ids` per-assembly (it's local already after Task 6/7) OR key it `(instance_id, ep_id)`.

- [ ] **Step 3: Implement / confirm** — ensure `seen_ep_ids` and `seen_files` never mix across instances (per-assembly locals). `seen_files` (canonical) is already instance-safe.

- [ ] **Step 4: Run → PASS** + characterization green.

- [ ] **Step 5: Commit** `git commit -m "feat(#161): dedup isolation across instances (phase 2 seam 4)"`

---

## Task 9: Full suite + gates + back-compat sweep

**Files:** none (verification).

- [ ] **Step 1: Full suite** — `python -m pytest -q`. Expected: all green (the Phase-1 baseline + new Phase-2 tests). Investigate any red; characterization MUST be green.
- [ ] **Step 2: Three lint gates** — `ruff check src tests && ruff format --check src tests && bandit -q -r src` (bandit on Windows: `PYTHONIOENCODING=utf-8 bandit -q -r src -f json` to dodge the cp1252 arrow crash; assert no NEW findings in `coverage_engine.py`).
- [ ] **Step 3: Loop-stall sanity** — confirm the `asyncio.to_thread` offloads (#308) survived the refactor (grep `to_thread` in `build_coverage`); the per-instance loop must not reintroduce on-loop SQLite/FS work.
- [ ] **Step 4: Commit** (if any gate fix) `git commit -m "test(#161): phase 2 full-suite + gates green"`

---

## Done criteria

- Characterization test green throughout (single-instance byte-identical).
- `build_coverage` fans out per instance; episode 101 in both Sonarrs → two distinct library-tagged rows.
- Rows emit `library` label; `sources` reports per-instance health.
- Full suite + 3 gates green. Loop offloads intact.

## Out of scope (Phase 3 / Phase 4 — do NOT build here)

- Writeback routing (subtitle upload, blacklist, episodeFile language PUT, `_bazarr_sync_task_id`) — **Phase 3**.
- Any UI rendering (coverage dropdown filter, labels, per-instance Health dots, library layout, Settings▸Instances) — **Phase 4** (see `2026-06-26-161-multi-instance-ux-surface-audit.md`).
