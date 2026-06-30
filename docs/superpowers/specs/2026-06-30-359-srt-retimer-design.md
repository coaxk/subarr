# #359 (immune slice) — subarr-side SRT re-timer

**Issue:** [#359](https://github.com/coaxk/subarr/issues/359) — tighten out-of-box subtitle quality (CPS).
**Date:** 2026-06-30
**Scope:** the **#171-immune slice** only — build + prove a base-agnostic re-timing post-pass. Explicitly defers production-pipeline wiring, corpus-wide tuning, per-language defaults, and the eventual bake.

## Problem & framing

CPS (chars-per-second) is the residual readability issue surfaced in Aftercare, worst in translate mode (translated text is longer than the original speech window, so no decoding/segmentation setting fits it under a comfortable CPS without more screen time). #359 names two CPS levers:

1. **Re-timing post-pass** — extend a hot cue into the available gap before the next cue. "The only thing that breaks the translate-mode floor; helps everyone, harmless for transcribe."
2. **stable-ts word-timestamp segmentation/regroup (#171)** — the source-side lever.

Lever 2 is **#171-entangled**: our entire current CPS control is `CUSTOM_REGROUP='cm_sl=84_sl=42++++++1'` ("strongpad"), a **stable-ts-only** regroup config that goes inert if/when upstream's `drop-stable-ts` is adopted. Lever 1 (re-timing) operates on **cue timestamps**, which survive the stable-ts→faster-whisper transition — it's base-agnostic and is exactly the "CPS-padding pass" Path C would contribute upstream.

**This slice builds Lever 1**, the immune one. It does not touch `CUSTOM_REGROUP`.

## Decision: subarr-side pure SRT→SRT transform

Re-timing only needs each cue's start/end times + text length — all present in the SRT. So it's a pure SRT→SRT function in subarr, **not** a subgen change:

- **100% #171-immune** — operates on the final SRT; agnostic to how cues were produced (stable-ts regroup or Netflix segmenter).
- **Prove-first** — no image rebuilds; validated in-process against the existing readability metrics. Matches #359's "off-app, prove it, then bake" method.
- **On-brand** — "subarr owns end-to-end quality"; reuses `subtitle_readability` instead of duplicating SRT logic in subgen.
- The *bake* (a subgen-side default, or the upstream Path C PR) becomes an explicit follow-on once the lever is proven — not a blocker, and not a footgun.

## Components — new `src/subarr/subtitle_retime.py`

Reuses `Cue` / `parse_srt` / `Cue.cps` / `Cue.duration` from `subtitle_readability` (read-only linter). Adds the missing serialize-half + the transform:

- `render_srt(cues: list[Cue]) -> str` — serialize cues back to SRT text (timestamps `HH:MM:SS,mmm`, blank-line separated, re-indexed 1..N).
- `@dataclass(frozen=True) RetimeParams` — the tunable knobs with placeholder defaults (arena-tuned in the follow-on, NOT hand-tuned here):
  - `target_cps: float = 17.0` (comfortable target)
  - `min_cue_ms: int = 1000` (kill sub-1s micro-cues)
  - `min_gap_ms: int = 100` (inter-cue gap always preserved)
  - `max_cue_ms: int = 7000` (Netflix max display)
- `retime_cues(cues: list[Cue], params: RetimeParams = RetimeParams()) -> list[Cue]` — pure transform, returns new `Cue`s with adjusted `end_ms`.
- `retime_srt(srt_text: str, params: RetimeParams = RetimeParams()) -> str` — `parse_srt → retime_cues → render_srt`.

## Algorithm (`retime_cues`)

Walk cues in start order. For each cue, **extend only its end forward** — never move the start, never shorten, never create an overlap:

1. **Available boundary** `bound_ms`:
   - if there is a next cue: `next.start_ms - min_gap_ms`
   - else (last cue): `cue.end_ms + (max_cue_ms - cue.duration_ms)` i.e. allow growth up to `max_cue_ms`.
   - Always also cap the new end at `cue.start_ms + max_cue_ms`.
   - If `bound_ms <= cue.end_ms` (no room, or input already overlaps), the cue is returned unchanged.
2. **Min-duration pad:** desired_end = `max(cue.end_ms, cue.start_ms + min_cue_ms)`.
3. **CPS extension:** if `cue.cps > target_cps`, `cps_end = cue.start_ms + round(chars / target_cps * 1000)`; `desired_end = max(desired_end, cps_end)`.
   - `chars` = the same displayable-char count `Cue.cps` uses (reuse, don't redefine, so the decision and the target agree).
4. **Clamp:** `new_end = min(desired_end, bound_ms, cue.start_ms + max_cue_ms)`; `new_end = max(new_end, cue.end_ms)` (never shorten).
5. Emit the cue with `end_ms = new_end`.

**Best-effort:** when the gap is too small to reach `target_cps`, the cue takes the partial extension available; the residual is what Aftercare monitors. Cues already ≤ target and ≥ min duration pass through untouched. **Idempotent:** running on already-re-timed output yields identical output (extensions are monotonic and clamped).

## Proving it (this slice's deliverable)

A measurement test using `subtitle_readability.analyze_srt` before/after on representative SRT fixtures (the high-CPS translate-mode sample is the key case):
- **median CPS decreases**, **%cues >25 CPS decreases**, **sub-`min_cue_ms` micro-cue count → 0**,
- **zero new overlaps** introduced (an overlap regression is a hard fail),
- a transcribe-mode sample with comfortable CPS + adequate gaps is **left ~unchanged** (harmless when not needed).

Full cross-clip arena corpus tuning (picking the real `RetimeParams` defaults across translate/transcribe × languages incl. JP→EN, English SDH) is the **follow-on**, using this proven mechanism.

## Testing (TDD)

`retime_cues` unit tests:
- hot cue (cps>target) with a large following gap → end extended toward the target-cps end; resulting cps ≈ target.
- micro-cue (duration < min_cue_ms) with gap → padded to `min_cue_ms`.
- hot cue with **no** gap (next cue immediately follows) → unchanged (residual).
- min-gap preserved: extended end ≤ `next.start_ms - min_gap_ms` always.
- never exceeds `max_cue_ms`; never shortens a cue.
- last cue with no successor → grows up to `max_cue_ms`.
- overlapping input cues → not made worse (no new/!larger overlap).
- idempotency: `retime_cues(retime_cues(x)) == retime_cues(x)`.

`render_srt`: round-trips (`parse_srt(render_srt(cues))` preserves times/text); correct `HH:MM:SS,mmm` formatting; 1-based re-indexing.

`retime_srt`: end-to-end on a fixture string → fewer CPS warnings via `analyze_srt`.

## Acceptance (this slice)

1. `retime_srt` measurably reduces median + %>25 CPS and zeroes micro-cues on the translate-mode fixture, with no new overlaps (proven via `analyze_srt`).
2. Transcribe-mode comfortable input passes through essentially unchanged.
3. Pure, fully unit-tested, idempotent; no `CUSTOM_REGROUP` / stable-ts coupling; ruff clean.

## Out of scope (explicit follow-ons)

- Production-pipeline wiring (where `retime_srt` runs in the completion/upload flow).
- Corpus-wide arena tuning + the per-language `RetimeParams` matrix.
- Per-language guidance docs.
- The bake: subgen-side default, or the Path C upstream contribution.
- Start-time extension (pulling a cue's start earlier into the preceding gap) — forward-only for v1; revisit if the corpus shows it's needed.

## Risk tier

**Tier-0/1** — a new, pure, self-contained module; no auth/writeback/data-model/cross-service surface. Read-diff review suffices.
