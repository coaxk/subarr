# Subarr v1.2 — Session Handoff & Release Plan

> **New session: start here.** Then do a full codebase familiarization pass
> (§7) and read `docs/audio-lang-tuning-subsystem-map.md` before writing code.
> This handoff is dense on purpose — a lot of interconnected work landed in one
> long session and the remaining 1.2 tasks depend on understanding it.

**Date:** 2026-06-06 · **Branch:** `feat/131-arena-ui` (coaxk/subarr, solo repo) ·
**Version:** `1.1.0` in `pyproject.toml` + `src/subarr/__init__.py` (→ bump to `1.2.0` at release) ·
**Dev box:** `subarr-next` @ **9923** (NEVER 9922 = prod).

---

## 0. Read-me-first rules (non-negotiable)
- **Test on 9923, never 9922.** Prod is sacred.
- **Deploy = bind-mount + restart**, not `docker cp`. Backend: `wsl -e bash -lc "docker restart subarr-next"`. Frontend: `npm run build:frontend` first, then restart. (`chrome.jsx` shared → rebuilds all bundles.)
- **Restart cancels in-flight arena sweeps + audit scans.** Warn Judd before deploying if he's mid-test. Sweeps → `reconcile_interrupted` (status `error`); audit findings are durable + resumable (mtime-skip).
- **Git for this repo: PowerShell** (WSL fails on Windows-path worktrees). **`gh` is authed in the default shell.** Docker is in **WSL** (`wsl -e bash -lc "docker …"`). Solo repo → `gh pr merge --admin` to bypass branch protection.
- **Never let subagents edit existing UI components** (JSX). Backend/tests/docs are fine to delegate; UI is hand-done.
- **Marketing/announcements live off-git** (`C:\Projects\_workstreams\subarr-landscape\`), humanized, **no em-dashes**.
- **TDD**: write the failing test first (see `MEMORY.md` testing-protocol). Assert body content, not just status codes.

---

## 1. Where we are
The whole **audio-language verification + Tuning Lab + #155 library audit** stack is built and on `feat/131-arena-ui` (46 commits ahead of origin at handoff time — **push pending**, see §6). It is **NOT yet merged to main** — Judd reviews `feat/131` before the 1.2 cut.

At handoff a **library audit scan was running live** on 9923 (≈184–533 files, ~100+ findings) on the *previously deployed* code. The latest commits (Tier-2, legend, progress bar, deep-scan toggle, last-scanned, multi-track display fix) are **built + green but not yet deployed** — deploy was deliberately deferred so the running scan finishes. **First physical step next session: confirm the scan finished, then deploy + live-verify (§3.1).**

### Done for 1.2 (on `feat/131`)
- **Tuning Lab / arena (#131, #65)** — config sweeps over subgen `/asr` (path-input + per-request kwargs, subarr-subgen v4.10+), strata-clip sampling (silero VAD), tournament QE judge, cross-clip aggregate + confidence, per-language **herd view** (#26 → addresses much of #146), single-flight concurrency cap.
- **Audio-language verification subsystem** — robust per-chunk detection; `resolve_source_language` classifier (mislabel / bilingual / multitrack / confused); manual **set-language** reusing the audio player; **Audio language issues** panel.
- **#155 library audit** — Phase 1 (harvest findings from completed sweeps, zero GPU) + Phase 2 (opt-in, throttled, GPU-polite, resumable background walker). **Tier-2 feedback** (unanimous mislabel → `whisper-robust` verification, conf 0.7 / risky 0.45). **Scan UI**: legend, progress bar, deep-scan toggle (coverage vs entire library), last-scanned summary, **multi-track shows real track langs** (`DE + RU`).
- **Burndown merged**: #119 (telemetry), #87 (subgen webhook push), #69 (series-lang intent), #104 (coverage perf), #75 (in-app credential editing), #79 (forced-sub), #147 (flag SVGs), #151, favicon fix (#849 selfhst/icons).

See `docs/audio-lang-tuning-subsystem-map.md` for the full technical map.

---

## 2. The 1.2 release plan (ordered)
1. **Deploy + live-verify the in-flight commits** (§3.1) — the legend/bar/toggle/Tier-2/multitrack work has only been unit-verified.
2. **#138 — Lighthouse/best-practices perf pass** (§3.2). Spec is ready-to-build. **Must live-verify in a browser** (CSP can silently break rendering).
3. **Cosmetics** (§3.3): healthcheck port, per-track source label.
4. **Version bump → 1.2.0** (`pyproject.toml`, `src/subarr/__init__.py`; UI version string).
5. **CHANGELOG finalize** (the `[1.2.0] - Unreleased` section exists — date it, fold in #138 + this session's additions).
6. **Board triage** (§4): close what shipped, label the federated items v2.
7. **Judd reviews `feat/131` → merge to main** (`--admin`), tag `v1.2.0`, let `release.yml` build the GHCR image.
8. **Release announcement** (off-git draft exists: `_workstreams/subarr-landscape/v1.2-release-announcement.md`) — humanize, post. Framing: *subarr does something the *arr metadata can't — it listens and tells you the truth about the track. "Verify, don't parrot," now visible in the UI.*

---

## 3. Remaining work — detail

### 3.1 Deploy + live-verify (FIRST)
After the scan finishes: `npm run build:frontend` → `wsl -e bash -lc "docker restart subarr-next"`. Then on 9923 verify:
- Legend renders; badges (🔎 mislabel / 🌐 bilingual / 🎚 multi-track) match the legend text.
- Progress bar fills during a scan; "Last scanned X ago · N checked" shows after.
- Deep-scan toggle: off = coverage scope (~533), on = `?scope=library` full walk.
- **Multi-track rows show real track langs** (`DE + RU`), not a single heard lang. (Rows written by the OLD running scan will have empty `track_languages` until re-scanned — a fresh scan/resume repopulates them. Migration 011 adds the column on this deploy.)
- **Tier-2**: after a unanimous mislabel, confirm a `whisper-robust` row appears in `/api/audio-lang/verifications` (conf 0.7; a JA/KO/ZH one at 0.45). Confirm the finding does NOT vanish (only `user` confirmation hides it), and that the corrected language shows in Coverage.
- Migration 011 applied cleanly (logs: `migration applied: 011_audio_audit_tracks`).

### 3.2 #138 — performance / best-practices (Lighthouse)
**Full spec below** (from a read-only audit). Backend only; **no JSX**. TDD where sensible; **the CSP needs real browser verification on 9923**.

- **Compression**: add `GZipMiddleware(minimum_size=1024)` (ships in starlette, zero new dep). Register so order ends up `BasicAuth → SecurityHeaders → GZip → routes`.
- **Security headers**: new `src/subarr/security_headers.py` ASGI middleware setting `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy: camera=(),microphone=(),geolocation=()`, and a **CSP**:
  `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https://github.com; connect-src 'self'; media-src 'self'; object-src 'none'; frame-ancestors 'none';`
  - `'unsafe-inline'` in **style-src only** is required by the `<style>` blocks in the 11 HTML pages. React `style={}` props compile to JS property assignments (NOT inline style attrs) so they're CSP-safe. `script-src 'self'` is clean (React is self-hosted under `/static/v1/vendor/`).
  - CSP **risk**: anything that breaks → it's the `<style>` blocks or the Google Fonts origins. Verify fonts load + pages render after deploy.
- **Favicon**: add a `/favicon.ico` route serving the existing `static/v1/favicon-32.png` (browsers hit `/favicon.ico` unconditionally; currently 404 on the homelab instance). Files already exist (`favicon.svg`, `favicon-16/32/192/512.png`, `site.webmanifest`).
- **Cache-Control**: `RevalidatingStaticFiles.get_response` currently sets blanket `no-cache`. Keep `no-cache` for **bundles + HTML** (filenames are NOT content-hashed — `immutable` would serve stale bundles across deploys). Add `max-age=604800, must-revalidate` for **immutable-ish** assets: `/static/v1/vendor/`, `/static/v1/favicon-*`, `/static/v1/favicon.svg`, `/static/v1/flags/`, `/static/v1/og-card.png`. **Do NOT use `immutable` on unhashed files.**
- **Middleware order** in `app.py`: register `GZip` first, then `SecurityHeaders`, then existing `BasicAuth` last (FastAPI wraps last-added = outermost).
- **Decisions for Judd** (note in PR, don't block): (a) brotli later via optional `[brotli]` extra; (b) self-host Google Fonts to drop the two font origins from CSP + go fully offline-capable; (c) optionally extract `<style>` blocks to CSS to remove `'unsafe-inline'`; (d) a true multi-size `.ico` (legacy IE only — skip).
- **`.gitattributes`/bundle-drift gate**: not affected (no new bundles). Don't reformat existing bundles.

### 3.3 Cosmetics (cheap, do alongside #138)
- **subarr-next healthcheck pings 9922** (prod port) → container shows `(unhealthy)` cosmetically. Fix the healthcheck URL to 9923 in the compose for `subarr-next` (needs a recreate, not just restart — schedule when convenient, it's harmless).
- **Per-track sweep source label** reads `"user"` for fanned-out track runs; should read `"track"` (set in the fan-out path so it's distinguishable in the herd). See `routers/arena.py` `/run` fan-out + `arena_service` source field.

---

## 4. Board triage (open issues → buckets)
**Ship-and-close as part of 1.2** (verify first):
- **#131** tuning lab (drive sweeps in-app, judge, adopt per-language winner) — core built; "adopt winner" UI is the last sub-piece (see §5 banked).
- **#155** library-wide audio audit — DONE.
- **#65** in-app Whisper tuning lab — DONE (== #131).
- **#88** subarr-subgen per-request kwargs channel — DONE (v4.10). Close.
- **#90** cross-check audio funnel against subgen detect-language — effectively DONE by the audit walker + Tier-2; confirm + close.
- **#146** global recipe leaderboard — partially covered by the herd view (#26); decide if the "overall" ranking is in-scope for 1.2 or a fast-follow.

**1.2 work item:** **#138** perf (above).

**v1.2-labelled but really the FEDERATED epic → v1.3/v2** (gated on telemetry scale + the QE summit):
- **#67** cross-install kwargs aggregation, **#68** "use community-best for <language>" one-click, **#124** federated tournament loop (epic), **#123** QE/adequacy summit, **#95/#101/#102** structured data capture + analytics + encryption. These are the **North Star** but NOT 1.2. Keep `v1.2` labels only on what actually ships; relabel the rest `v2`.

**Backlog / icebox** (not 1.2): #144 scheduled auto-walks, #140 mis-grouped-series detector, #136 DB retention, #134 multi-root, #118 lang-code normalization hardening, #117 settle-window, #116 throttled backfill, #89 teach subgen skip-vars, #83 TrueNAS, #82 Unraid, #81 promo reel, #72/#71 Jellyfin/media-server abstraction, #70 arm64, #66 queue mutation, #64 provider leaderboard. Triage against 1.2 only if Judd flags one.

**Folder-path wanted rows** (memory `project_subarr-folder-path-wanted-rows.md`): 61 Bazarr-wanted episodes resolve to show FOLDER not file → stuck in probe-gate "Analyzing". Needs Judd's verification-chain design review; not blocking 1.2.

---

## 5. Gold & banked ideas from this session (interconnected — read before touching the audit/coverage code)
- **"Verify, don't parrot" made visible.** The audio-issues panel + Tier-2 is the productized thesis: subarr *listened* and disagreed with the tag. This is the 1.2 marketing hook and the data substrate for the federated North Star.
- **North Star (Judd):** scan data → telemetry at global scale → curated per-language settings → "adopt winner locally" + "adopt global winner" back to the userbase → iterate forever. 1.2 = the **local** half (lab + audit + Tier-2 dataset). The federated half (#67/#68/#124/#123) is v2, gated on the **QE summit** (a trustworthy reference-free quality judge — `project_subarr-positive-quality-gap.md`) so we never crowd-aggregate on a judge that only catches failure modes.
- **Deep-scan toggle = opt-in data contribution.** Off by default (coverage scope); users who enable entire-library scanning generate more userbase data. Already wired (`?scope=library`).
- **Bilingual clustering is REAL signal, not noise.** High bilingual rates concentrated on episodes of the same foreign show = border-region productions that genuinely swap languages. Validated by Judd against his ear. Don't "fix" the bilingual detector to suppress these.
- **Don't assert a show's language from its title** — read Sonarr/VLC/Whisper. `ar`=Arabic (not Armenian). Bilingual-in-one-track ≠ two audio tracks.
- **Tier-2 confidence asymmetry is deliberate:** non-risky 0.7 (drives override), risky JA/KO/ZH 0.45 (display-only, below the override gate) — a wrong CJK override yields unusable Whisper output, so it must wait for human confirm.
- **subgen relationship** (`project_subarr-subgen-relationship-roadmap.md`): vanilla-compat subgen = the free hook; subarr-subgen patch-stack = the conversion. Keep decoupled; bundle-but-separable later. Don't hard-fork yet.

---

## 6. Bring-up-to-date checklist (do at end of THIS session)
- [ ] Commit all built work to `feat/131-arena-ui` (done through `cb49a01`).
- [ ] Push `feat/131-arena-ui` to origin (46+ commits).
- [ ] `MEMORY.md` + `project_subarr.md` updated with the v1.2 status (done — see memory).
- [ ] These two docs committed (`docs/handoff-v1.2.md`, `docs/audio-lang-tuning-subsystem-map.md`).
- [ ] Do NOT deploy/restart while Judd's scan is running.

---

## 7. New session: first moves
1. Read this handoff + `docs/audio-lang-tuning-subsystem-map.md` + `CLAUDE.md` + `MEMORY.md` (testing-protocol, deploy gotchas).
2. **Full codebase familiarization** — don't trust this doc alone; the code moved fast. Walk: `app.py` lifespan wiring → `routers/` → `arena*.py` / `audio_audit*.py` / `audio_lang_store.py` / `coverage_engine.py` → `static/v1/home-hifi/arena.jsx` + `coverage.jsx`. Run the suite once: `cd C:\Projects\subarr-ui; $env:PYTHONPATH="src"; python -m pytest -p no:cacheprovider -q` (expect ~575 pass; the lone `test_arena_router.py::test_set_language_updates_run` failure is a KNOWN in-suite ordering artifact — green in isolation).
3. Confirm the scan finished, then **deploy + live-verify (§3.1)**.
4. Build **#138 (§3.2)** with live browser verification.
5. Cosmetics (§3.3) → version bump → CHANGELOG → board triage → hand to Judd for `feat/131` review + 1.2.0 tag.

**Ask Judd before**: merging `feat/131` to main; posting the announcement; any scope change to the federated (v2) items.
