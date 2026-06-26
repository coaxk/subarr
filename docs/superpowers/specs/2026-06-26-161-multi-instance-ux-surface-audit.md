# #161 Multi-instance — UX surface audit

**Date:** 2026-06-26 · **Issue:** [coaxk/subarr#161](https://github.com/coaxk/subarr/issues/161)
**Purpose:** enumerate EVERY UI surface (from the actual nav in `chrome.jsx`, not memory) and classify how multiple Sonarr/Radarr/Bazarr instances affect it, so nothing renders wrong or under-labelled. Companion to the Phase 2 design (`2026-06-26-161-phase2-coverage-merge-design.md`) and the epic design (`2026-06-25-161-multi-instance-design.md`).

## The cross-cutting enabler (Phase 2)

Most display surfaces consume coverage/queue/review/aftercare rows. Every row already
carries `@<slug>` in its canonical, but the row dict has **no clean `library` field and
no instance label**. **Phase 2 will emit `library` (slug + human name) on the merged
rows** (instance name is derivable from the library's binding). This single addition is
the hook every "needs-label / needs-filter" surface below depends on — cheap now,
expensive to retrofit. The actual rendering (labels, filters, layouts) is **Phase 4 UI**.

**Library/coverage filter UX decision (Judd, 2026-06-26):** a **dropdown** filter
(All / TV / Anime / …), not grouped sections.

## Surface matrix (every nav tab + sub-rail)

| # | Surface (route) | Multi-instance impact | Classification | Needed | Phase |
|---|---|---|---|---|---|
| 1 | **Overview ▸ Dashboard** (`/home`) | gaps/queue/review/aftercare counts become cross-instance **sums** | Aggregate (works) | totals fine; per-stack breakdown = nice-to-have | 4 (optional) |
| 2 | **Overview ▸ Health** (`/health`) | one set of integration status dots today | **Needs per-instance** | per-instance dots (sonarr-tv ✓, bazarr-anime ✗) — fed by Phase 2 per-instance `sources` | 4 |
| 3 | **Overview ▸ Schedule** (`/rules`) | the coverage walk now fans across instances (Phase 2) | Mostly unaffected | one cadence covers all instances; confirm no per-instance schedule wanted | 4 (confirm) |
| 4 | **Overview ▸ Audit log / Activity** (`/file-modal`) | provenance entries span instances | **Needs label** | library/instance label on entries | 4 |
| 5 | **Gaps ▸ Coverage** (`/coverage`) — the Gaps page | rows span instances; THE primary surface | **Needs label + filter** | library column + dropdown filter (All/TV/Anime) | 4 |
| 6 | **Gaps ▸ Queue** (`/queue`) | pending/submitted jobs span instances | **Needs label** (+ filter optional) | library/instance label; writeback routing is Phase 3 | 4 |
| 7 | **Gaps ▸ Review** (`/review`) | audio-lang rows span instances | **Needs label** (+ filter) | library/instance label; verify/swap writeback is Phase 3 | 4 |
| 8 | **Gaps ▸ Aftercare** (`/aftercare`) | post-job rows span instances | **Needs label** | library/instance label | 4 |
| 9 | **Gaps ▸ Tuning Lab** (`/arena`) | per-file sweeps, path/library scoped | Mostly unaffected | track-mismatch arena view uses coverage → optional label | 4 (minor) |
| 10 | **Gaps ▸ Activity** (`/file-modal`) | same as #4 (shared route) | Needs label | (covered by #4) | 4 |
| 11 | **Gaps ▸ Logs** (`/logs`) | Docker/app/subgen logs are singleton | Singleton | unaffected | — |
| 12 | **Gaps ▸ Rules** (`/rules`) | language rules | Confirm scope | confirm rules stay **global per-language**, not per-instance | 4 (confirm) |
| 13 | **Library ▸ Browse** (`/library`) | media tree across libraries × instances | **Needs layout** | library **dropdown** filter (Judd's pick); tree already #134-library-aware | 4 |
| 14 | **Settings ▸ Integrations** (`/settings#integrations`) | the config surface | **Config (known)** | Settings ▸ Instances list + binding + resolved-topology | 4 (epic) |
| 15 | **Settings ▸ Scheduler** (`/rules`) | shared with #3 | Mostly unaffected | (covered by #3) | 4 |
| 16 | **Settings ▸ Telemetry** (`/settings#telemetry`) | telemetry payload | Confirm scope | confirm aggregate (not per-instance) reporting | 4 (minor) |
| — | **GPU card, subgen tile/setup** (`/home`, `/subgen-setup`) | singleton services | Singleton | unaffected | — |

## What this means by phase

- **Phase 2 (this slice):** emit `library` (slug + name) on merged rows + per-instance
  `sources` health (already in the Phase 2 design §6). No UI rendering. This unblocks
  everything below.
- **Phase 3:** writeback routing (Queue/Review actions fire at the owning instance). No
  new display work, but it's why Queue/Review rows must carry instance provenance.
- **Phase 4 (UI):** render the labels + the **dropdown** filter on Coverage/Library
  (and reuse on Queue/Review/Aftercare/Activity); per-instance Health dots; Settings ▸
  Instances + resolved-topology; optional dashboard per-stack breakdown. Confirm
  Rules/Telemetry/Schedule stay global.

## Open confirmations (cheap, for Phase 4)
- Rules are **global per-language** (not per-instance) — assume yes.
- Schedule/coverage-walk is **one cadence across all instances** — assume yes.
- Telemetry reports **aggregate** (not per-instance) — assume yes.
