# #161 Phase 3 — Writeback routing across instances — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development per task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Route every writeback (subtitle upload, blacklist, episodeFile/movieFile language PUT, Bazarr task triggers, download-candidate accept) to the arr/Bazarr instance that owns the row's library, via `clients_for(bundle, canonical)`. Single-instance byte-identical (resolves to instance 0).

**Design:** `docs/superpowers/specs/2026-06-26-161-phase3-writeback-routing-design.md`.

**Tech stack:** Python 3.11+, pytest, asyncio. Worktree: `C:\Projects\subarr-161-p2`, branch `design/161-phase3-writeback-routing` (rename/branch for impl as desired). Editable install — verify `python -c "import subarr,os;print(os.path.dirname(subarr.__file__))"` points at the worktree `src`.

**Critical gotchas:** repo `.py` are CRLF (LF git warnings benign); the blocking PostToolUse ruff hook flags F821/F841 on partial edits (add import+usage together; it does NOT auto-delete — F841/F821 only nag); run `ruff format` on heredoc-appended tests; completion_watcher / scheduler / audio_lang are background/event-loop paths — keep writes off the loop where they already are.

---

## Task 1: Writeback multi-instance test fixture (safety net)

**Files:** `tests/conftest.py`

- [ ] **Step 1:** Add a `writeback_stack` fixture: 2 Bazarr + 2 Sonarr + 2 Radarr instances (mirror `anime_stack_full`) whose mock transports RECORD each write (append `(method, path, json)` to a per-instance list reachable from the test). Library 0 (`""`) + an `anime` library bound to the `anime` instances. Expose the per-instance recorders.
- [ ] **Step 2:** Sanity test: a direct `clients_for(bundle, "@anime/x").bazarr` is the anime instance; `clients_for(bundle, "Show/x").bazarr` is instance 0.
- [ ] **Step 3:** Commit.

---

## Task 2: Subtitle upload (episode) + completion_watcher triggers → per-instance

**Files:** `src/subarr/completion_watcher.py`. Test: `tests/test_completion_watcher_*` / new.

- [ ] **Step 1: Failing test** — drive the watcher's upload path with `writeback_stack`; an `@anime/...` entry's subtitle upload + scan-disk trigger must hit the **anime** Bazarr recorder, a default entry must hit instance 0.
- [ ] **Step 2: Implement** — replace `self._bazarr` at the upload (~301) and trigger (~272, ~418) sites with `clients_for(self._bundle_provider(), entry.canonical_path).bazarr`. Replace `self._bazarr_task_id` with a `dict[str, str]` task-id cache keyed by bazarr instance id; discover per instance.
- [ ] **Step 3:** Run → PASS; single-instance still hits instance 0.
- [ ] **Step 4:** Commit.

---

## Task 3: Sonarr episodeFile language PUT + audio-lang Bazarr trigger → per-instance

**Files:** `src/subarr/routers/audio_lang.py`. Test: `tests/test_audio_lang*`.

- [ ] **Step 1: Failing test** — an `@anime/...` propagation PUTs episodeFile languages on the **anime** Sonarr and triggers the **anime** Bazarr.
- [ ] **Step 2: Implement** — `clients_for(bundle, canonical_path).sonarr` at ~157; thread `canonical_path` into `_trigger_bazarr_sync` and resolve `.bazarr` at ~220. Replace module global `_bazarr_sync_task_id` with a per-instance dict cache.
- [ ] **Step 3:** Run → PASS + single-instance unchanged.
- [ ] **Step 4:** Commit.

---

## Task 4: Bazarr blacklist (episode + movie) → per-instance (schema + resolve)

**Files:** `src/subarr/routers/blacklist.py`. Test: `tests/test_blacklist*`.

- [ ] **Step 1: Failing test** — POST blacklist with `canonical_path="@anime/..."` hits the anime Bazarr; without a path → instance 0.
- [ ] **Step 2: Implement** — add optional `canonical_path` to the episode + movie blacklist request models; resolve `clients_for(bundle, canonical_path).bazarr` (fallback instance 0 when absent).
- [ ] **Step 3:** Run → PASS.
- [ ] **Step 4:** Commit.

---

## Task 5: Bazarr download-candidate accept (episode + movie) → per-instance

**Files:** `src/subarr/routers/arbiter.py`. Test: `tests/test_arbiter*`.

- [ ] **Step 1: Failing test** — accept with `canonical_path` routes to the owning Bazarr.
- [ ] **Step 2: Implement** — add optional `canonical_path` to `AcceptRequest`; resolve; fallback instance 0.
- [ ] **Step 3:** Run → PASS.
- [ ] **Step 4:** Commit.

---

## Task 6: Bazarr sync-disk standalone trigger → per-instance

**Files:** `src/subarr/routers/bazarr_sync.py`. Test: `tests/test_bazarr_sync*`.

- [ ] **Step 1: Failing test** — `SyncDiskRequest` with `canonical_path` triggers the owning Bazarr's task; absent → instance 0.
- [ ] **Step 2: Implement** — resolve the client from the (already-optional) `canonical_path`; reuse the per-instance task-id cache helper.
- [ ] **Step 3:** Run → PASS.
- [ ] **Step 4:** Commit.

---

## Task 7: Scheduler stale-disk poke → fan out per Bazarr instance

**Files:** `src/subarr/scheduler.py`. Test: `tests/test_scheduler*`.

- [ ] **Step 1: Failing test** — stale items across two libraries trigger BOTH Bazarr instances' scan-disk task (not just instance 0).
- [ ] **Step 2: Implement** — derive the set of Bazarr instances owning ≥1 stale item from the stale rows' canonicals; trigger each. Single-stack → one trigger, unchanged.
- [ ] **Step 3:** Run → PASS.
- [ ] **Step 4:** Commit.

---

## Task 8: Frontend — pass canonical_path from the row

**Files:** `src/subarr/static/v1/home-hifi/*.jsx` (blacklist-panel, arbiter/candidates, sync action) + rebuild bundles.

- [ ] **Step 1:** In each action that calls blacklist/arbiter/sync, include the row's `file_canonical_path` (or `canonical_path`) in the request body.
- [ ] **Step 2:** `node scripts/build-frontend.mjs`; commit ONLY the touched `*.jsx` + their `*.bundle.js` (CI `check:frontend` gates `*.bundle.js`/`*.html`).
- [ ] **Step 3:** Commit.

---

## Task 9: Full suite + gates + writeback characterization

**Files:** none (verification).

- [ ] **Step 1:** `python -m pytest -q` all green.
- [ ] **Step 2:** Gates — `ruff check src tests && ruff format --check src tests && PYTHONIOENCODING=utf-8 bandit -q -r src`.
- [ ] **Step 3:** Confirm single-instance writeback still targets instance 0 across all ops (the back-compat assertions).
- [ ] **Step 4:** Pre-merge review (Tier 2 — writeback to an arr is explicitly high-stakes in the review policy): multi-lens + failure-mode lens. Fix reals or file. Then PR + `--admin` merge after CI.

---

## Done criteria
- Every writeback resolves its instance from the row's canonical (fallback instance 0).
- Per-instance Bazarr task-id caches; scheduler fans out per instance.
- Single-instance byte-identical; multi-instance writes land on the owning instance (asserted by the recording fixture).
- Full suite + 3 gates green; Tier-2 review passed.

## Out of scope
- Movie subtitle upload wiring (needs `radarr_movie_id` on provenance entries) — **separate issue**.
- UI for per-instance management / health (Phase 4).
