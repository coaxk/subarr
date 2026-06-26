# #161 Phase 3 (writeback routing) — handoff → next session

**Written:** 2026-06-27, end of a long session (#369 hotfix + Phase 3 backend).

## TL;DR — START HERE
Phase 3 **backend (T1–T7) is DONE, committed, full-suite green**. Remaining: **T8 (frontend) + T9 (Tier-2 review + PR/merge)**. Plan: `docs/superpowers/plans/2026-06-26-161-phase3-writeback-routing.md`. Design: `docs/superpowers/specs/2026-06-26-161-phase3-writeback-routing-design.md`.

## State
- **Branch:** `design/161-phase3-writeback-routing` (worktree `C:\Projects\subarr-161-p2`), **8 commits ahead of `origin/main`**, rebased on current main (includes the #369 entrypoint fix + 2.2.1).
- **Editable install** points at this worktree's `src` (verify `python -c "import subarr,os;print(os.path.dirname(subarr.__file__))"`).
- **Full suite: 1372 passed, 6 skipped** (2026-06-27). Single-instance byte-identical throughout (each task's pre-existing tests stayed green).
- Commits: T1 `60f88a0` · T2 `1617fc6` · T3 `d69bf47` · T4 `0122e3b` · T5 `2a749bd` · T6 `7bfaaba` · T7 `7bd28a4` (+ design/plan docs `fdc1e96`).

## The pattern (established T2, reused everywhere)
Every writeback resolves its target instance from the row's canonical via the existing `clients_for(bundle, canonical)` / `bundle.client_for("bazarr"|"sonarr"|"radarr", library_for_canonical(canonical).<svc>_id)`. **No path / no binding → instance 0** = byte-identical single-stack.
- completion_watcher: `_bazarr_for(canonical)` helper; per-instance Bazarr task-id cache `_bazarr_task_ids` (keyed by `base_url`) via `_bazarr_task_for(bz)`; stuck-retry groups rows by owning Bazarr.
- audio_lang: `_propagate_to_sonarr` PUTs on the owning Sonarr; `_trigger_bazarr_sync(bundle, canonical_path)` + module-level per-instance dict `_bazarr_sync_task_ids`.
- blacklist / arbiter: routers gained an OPTIONAL `canonical_path` request field + a local `_bazarr_for(request, canonical_path)` (fallback instance 0).
- bazarr_sync: resolves from the existing optional `canonical_path`.
- scheduler: `_maybe_poke_bazarr_for_stale_disk` groups stale rows by owning Bazarr, fires one scan-disk per instance (`_discover_bazarr_scan_task(bz)`); cooldown stays global; return shape gained an `instances` list.

## Test infra
`tests/conftest.py::writeback_stack` — 2 Sonarr + 2 Radarr + 2 Bazarr instances with **recording** mock transports; returns `SimpleNamespace(bundle, calls)` where `calls[(svc, iid)]` is the list of recorded writes. The `/api/system/tasks` GET returns BOTH a scan-disk task (`series_full_scan_subtitles`/"Scan disk") and `update_series`/"Sync with Sonarr". Tests live in `tests/test_writeback_routing.py`.

## NEXT — T8 (frontend), then T9
**T8:** the UI actions that call blacklist / arbiter-accept / bazarr sync-disk must include the row's `file_canonical_path` (fallback `canonical_path`) in their request bodies, so the now-instance-aware backend routes correctly. Files: `src/subarr/static/v1/home-hifi/blacklist-panel.jsx`, the candidates/arbiter UI, and the sync-disk action. Then `node scripts/build-frontend.mjs` and commit ONLY the touched `*.jsx` + their `*.bundle.js` (CI `check:frontend` gates `*.bundle.js`/`*.html`). Backend fields already DEFAULT to instance 0, so the UI works untouched until T8 — no rush, but the multi-instance write path isn't complete without it. Do NOT use subagents on existing UI components (agent-file-safety).

**T9:** `python -m pytest -q` + 3 gates (`ruff check`, `ruff format --check`, `PYTHONIOENCODING=utf-8 bandit -q -r src`). Then **Tier-2 pre-merge review** — writeback-to-an-arr is the program's high-stakes category → multi-lens + failure-mode lens (a fresh read of the per-instance routing, task-cache keying, stuck/scheduler fan-out, and back-compat). Fix reals or file. Then PR + `gh pr merge --admin --merge`. Branch is solo-repo.

## Gotchas (carry in)
- **ruff PostToolUse hook strips an unused import (F401) on the SAME edit** — add `from ..paths import library_for_canonical` WITH a usage already present (add the helper/usage first, then the import). F841/F821 only nag (non-fatal), never auto-delete.
- Repo `.py` are CRLF (LF git warnings benign); `ruff format` heredoc-appended tests before commit.
- `C:\Projects\subarr` (owns main) is currently checked out on the merged `fix/369-entrypoint-chown-scope` branch — `git checkout main && git pull` there if working on main.

## Out of scope / deferred
- **#368** — movie subtitle upload (needs `radarr_movie_id` on provenance entries); Phase 3 routes the scan-disk fallback per-instance but the direct movie upload is still a stub.
