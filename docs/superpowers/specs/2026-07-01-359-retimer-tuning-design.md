# #359 tuning slice — provenance-fed re-timer parameter sweep

**Issue:** [#359](https://github.com/coaxk/subarr/issues/359) — out-of-box subtitle quality (CPS).
**Date:** 2026-07-01
**Scope:** build an off-app harness that sweeps `RetimeParams` across subarr's real subgen-produced subtitle corpus, run it, and recommend proven defaults. The **bake** (update defaults + flip `SUBARR_RETIME_ENABLED` default on) is a small follow-up gated on the user's sign-off of the recommended params.

## Context & method

PR #401 shipped the pure re-timer (`subtitle_retime.retime_srt`); PR #402 wired it into the completion flow behind `SUBARR_RETIME_ENABLED` (default off). The re-timer's `RetimeParams` defaults are placeholders. #359's method is "off-app, prove it, then bake" with cross-corpus rigor (a winner is only trustworthy across *separate* samples, not one).

The re-timer is a **deterministic SRT post-pass**, so its tuning is far lighter than the #131 arena (which re-runs subgen recipes): apply `retime_srt(params)` across a corpus of existing SRTs and measure readability deltas — no subgen, no video, no judges.

**Corpus = subarr's own completed subgen jobs**, sourced from the **`subs_generated` ledger** (the authoritative "subarr made this via subgen" list — 2112 completed rows), NOT a filesystem scan (which would sweep in Bazarr provider subs). This is the local bootstrap of the federated-tuning loop (#124): the install's own history is the corpus.

## Corpus gathering (the robustness-critical part)

For each completed `subs_generated` row:
1. Resolve `canonical_path` → the **original subgen sidecar** on disk. Use the `_find_srt_sidecar` logic but **exclude subsyncarr variants**: the plain `<base>.<lang>.srt`, never `<base>.<lang>.ffsubsync.srt` / `.alass.srt` / other engine-suffixed files (subsyncarr re-syncs the original with 4 engines — timing-shifted derivatives with identical CPS; including them would 5× the same subs).
2. Read it; skip if missing/empty/unparseable.
3. **Bazarr-replacement guard:** if `aftercare_results` has a row for this `canonical_path`, compare the on-disk `cue_count` (via `parse_srt`) to the recorded `cue_count`. A material mismatch (e.g. differ by more than a small tolerance) means the on-disk file was replaced (likely a Bazarr provider download) after subarr made it → **skip** it. This directly closes the "unlikely but possible" provider-replacement edge. (No aftercare row → keep, best-effort.)

The surviving set is the corpus. A `--dir <path>` mode bypasses the ledger and sweeps any folder of `.srt` files (for testing + ad-hoc corpora).

## Components

### 1. Pure sweep core — `retime_sweep(srt_texts, param_grid) -> list[SweepRow]`
For each `RetimeParams` combo in the grid, apply `retime_srt` to every corpus SRT and aggregate readability (via `subtitle_readability.analyze_srt` / `parse_srt` + `Cue.cps`) into one `SweepRow`:
- `params` (the combo)
- `median_cps_before`, `median_cps_after`
- `pct_over_critical_before/after` (cues > `CRITICAL_CPS`=25)
- `pct_over_comfortable_before/after` (cues > `MAX_CPS`=20)
- `micro_cues_before/after` (cues < `MIN_DURATION_S`)
- `too_long_introduced` (cues > `MAX_DURATION_S` in after but not before) — **the over-extension guard**
- `mean_added_ms` (mean total screen-time added per sub)
- `subs_changed` (how many subs the combo actually modified)

Pure + deterministic → unit-tested on synthetic fixtures with known CPS.

### 2. Corpus adapters
- `corpus_from_ledger(db_path, media_root, ...) -> list[(canonical_path, srt_text)]` — the provenance-fed gather above (ledger query + sidecar resolve + subsyncarr exclusion + aftercare guard).
- `corpus_from_dir(path) -> list[(name, srt_text)]` — the `--dir` fallback.

### 3. CLI — `scripts/retime_tune.py`
`python scripts/retime_tune.py [--db PATH] [--media-root PATH] [--dir PATH] [--limit N]` → gathers the corpus, runs the sweep over the fixed grid, prints a ranked table (best CPS reduction, penalized by `too_long_introduced`) with a no-op baseline row, plus corpus size + how many were skipped (and why). Read-only — never writes to the corpus or DB.

### 4. Param grid (fixed for this sweep)
`target_cps ∈ {15.0, 17.0, 20.0}` × `min_cue_ms ∈ {833, 1000, 1200}`; `min_gap_ms=100`, `max_cue_ms=7000` held constant. 9 combos + the baseline.

## Data flow

```
subs_generated (completed) ──► resolve original subgen .en.srt (exclude subsyncarr) ──►
  aftercare cue_count guard (skip replaced) ──► corpus [(path, srt_text)]
    ──► retime_sweep(texts, grid) ──► ranked SweepRow table ──► human picks the knee ──► (bake, follow-up)
```

## Running it (this session, by the controller)

Run `scripts/retime_tune.py` against the live subarr-next DB + media (via the container) over the ~2112-sub corpus. Analyze the ranked table for the **knee** — the params giving the largest drop in %>25 CPS and median CPS before `too_long_introduced` / `mean_added_ms` climb into over-extension. Bring the user a recommended `RetimeParams` with the numbers behind it.

## Error handling

- Read-only throughout: never mutates corpus SRTs or the DB.
- Per-sub failures (unparseable SRT, missing sidecar) are skipped + counted, never fatal — a couple of junk files can't sink a 2000-sub sweep.
- Missing DB / media path → clear error + non-zero exit (the `--dir` mode needs neither).

## Testing

- **`retime_sweep`** (pure): synthetic fixtures with known CPS → assert median/%-over/too_long/added-ms aggregates and the ranking are correct; a comfortable-only corpus yields ~zero change across all combos; a hot corpus shows CPS dropping as `target_cps` lowers.
- **`corpus_from_dir`**: a temp folder of `.srt` files → returns their texts; skips a malformed one.
- **`corpus_from_ledger`**: a temp SQLite DB with a `subs_generated` row + a temp sidecar → returns it; a subsyncarr-suffixed sibling is **excluded**; an `aftercare_results` `cue_count` mismatch **skips** the sub; a missing sidecar is skipped.
- The real run is a manual invocation (not a unit test).

## Acceptance

1. `retime_tune.py --dir <fixtures>` prints a correct ranked sweep table (verified against hand-computed fixture metrics).
2. The ledger adapter gathers only original subgen subs (subsyncarr variants excluded; Bazarr-replaced subs skipped via the aftercare guard).
3. A real run over the live corpus produces a defensible recommended `RetimeParams` (the knee), reported with its before/after numbers.
4. Full suite green; ruff clean.

## Out of scope (follow-ons)

- **The bake** — updating `RetimeParams` defaults to the recommended values + flipping `SUBARR_RETIME_ENABLED` default to on. A tiny separate PR after the user signs off on the params (it's a behaviour flip that deserves its own review).
- Per-language `RetimeParams` matrices (a global default first; stratify later if the sweep shows languages diverge sharply).
- Any user-facing UI (the arena stays the power-user surface; this is off-app tuning).
- The subgen-side / Path-C upstream bake.

## Risk tier

**Tier-0/1** — a read-only off-app analysis tool (`scripts/`), pure sweep core, no writes to subs/DB, no app-runtime surface. Read-diff review suffices. The behaviour-changing bake is deferred and separately reviewed.
