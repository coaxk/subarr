# #317 Slice C — Subtitle-controls discoverability

**Date:** 2026-06-22
**Issue:** #317 (Slice C — the discoverability pass; Slices A blacklist + B forced-sub action shipped)
**Approach:** Signpost + cross-link. Keep every control where it lives; add a directory hub + targeted cross-links. **No control is moved or duplicated** (avoids re-render/state-sync risk and rewrites of existing components).

## Problem

The controls that govern subtitle behaviour work but are scattered and undiscoverable, and no page explains how they interrelate:

- **Forced subtitles** — Coverage page (forced-only bucket + the new "Transcribe full sub" action, Slice B).
- **Ignored titles (#316)** — Library tree + Review page.
- **Language rules (#226)** — *declared* in Review ("Remember for future downloads"), *managed/revoked* in Settings › Language rules.
- **Audio-language review** — Review page (+ Coverage pending-review banner).

A user looking to "stop subarr re-asking about this show's language" or "ignore this whole series" has no single place that points them to the right control.

## Goals

1. One **hub** on the Rules page that names each control family in Bazarr-familiar terms, says what it does in one line, and links to where it lives.
2. The few missing **cross-links** "where the instinct lands", so each control is reachable from the page where a user would look for it.
3. Zero behavioural change to the controls themselves. Additive UI only.

## Non-goals

- Moving/embedding/duplicating controls onto the Rules page.
- Rewriting existing components (Library tree, Review rows, Settings panels).
- Any new backend endpoint or data model.

## Design

### 1. "Subtitle controls" hub card (Rules page)

A new collapsible card rendered at the **top of the Rules page**, above the Build/Test/Deploy editor. Purely a signposted directory — one row per control family:

| Label | One-liner | Link target |
|---|---|---|
| **Forced subtitles** | Files whose only English sub is forced (covers foreign dialogue only) — transcribe a full sub per file. | Coverage (forced-only bucket) |
| **Ignored titles** | Skip a whole series or movie from subtitle processing. | Library |
| **Language rules** | Tell subarr a show's real audio language so it stops re-asking. Declared during audio-language review; managed in Settings. | Settings › Language rules (primary) + Review (declare) |
| **Audio-language review** | Confirm a file's audio language when subarr isn't sure. | Review |

- Collapsible (default collapsed or open — match the Rules page's existing card convention).
- Each row: label + one-liner + a navigation button/link. Reuse existing button/link styles (`btn`/`btn-sm`/`btn ghost`) and CSS vars.
- The "Language rules" row carries two links (manage → Settings, declare → Review) since the family has two touchpoints.

### 2. Cross-links "where the instinct lands"

Small, additive links on existing pages (no component restructuring):

- **Settings › Language rules** empty-state: convert the existing plain-text "On the Review page…" into a real link to Review.
- **Review** (after bulk-assign with "Remember for future downloads"): add a "Manage language rules →" link to Settings › Language rules.
- **Coverage** forced-only bucket: a small "All subtitle controls →" link to the Rules hub.
- **Library / Review** ignore action: a link to the existing "ignored titles" list so users can find what they've ignored. (Review already has an ignored-titles panel; Library shows ignored rows inline — add the link where it's missing.)

### 3. Navigation / deep-linking

Cross-page nav is href-based (chrome nav rail uses page hrefs). Sub-section targets (Settings › Language rules panel, Coverage's forced bucket) need a deep-link. **Before inventing a mechanism, reuse subarr's existing one** — check for a tab/hash param pattern already used by Settings panels (the app already does cross-tab deep-linking elsewhere). If a hash/anchor pattern exists, use it; if not, the minimum viable target is the page top (still an improvement over no link). Document whichever is chosen.

## Components touched (additive only)

- `rules.jsx` — new `SubtitleControlsHub` card component + render at page top.
- `settings.jsx` — link in the Language-rules empty state.
- `review.jsx` — "Manage language rules" link post-declare.
- `coverage.jsx` — "All subtitle controls" link in the forced-only bucket.
- `library.jsx` / `review.jsx` — ignored-titles link (where missing).

## Testing

- Frontend (vitest): a focused test for any pure helper (e.g. the hub's link-target map) if one is extracted; otherwise the hub is presentational and verified by build + manual.
- Build: bundle rebuilds via the pre-commit hook; drift gate stays green.
- Manual (live verify, bundled into the Slice B/C deploy): each hub link and cross-link navigates to the right place.

## Out of scope / banked

- Deploy: rides on the single `v2026.05.3-r10` subgen tag + `:9008` upgrade already banked for Slice B. Slice C is subarr-only (no subgen change), so it just needs subarr main on `:9923`.
