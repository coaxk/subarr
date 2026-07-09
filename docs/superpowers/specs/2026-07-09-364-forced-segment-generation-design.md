# #364 — forced-segment generation: detect foreign scenes at import, emit a scoped `.forced.en.srt`

**Issue:** [#364](https://github.com/coaxk/subarr/issues/364) — external demand signal (r/Softwarr, r/sonarr; the closed #317 discoverability issue is separate).
**Date:** 2026-07-09
**Scope:** the full epic, designed end-to-end, then decomposed into shippable slices (see **Slicing**). The goal: for a file that is mostly one language (English, slice 1) with a **short embedded foreign-language scene** and no forced track, generate a scoped `<basename>.forced.en.srt` covering *only* those scenes, correctly timed and forced-flagged — targeted pre-processing at import, not full-movie transcription and not just-in-time-during-playback.

## Problem & framing (settled in brainstorming)

The "forced subtitle" gap: you only want subs for the handful of scenes where characters speak a non-primary language. Today's options are grab-a-forced-track-from-Bazarr (often missing) or transcribe-the-whole-movie (overkill, produces full subs not forced). Neither lands the want.

The instinctive **just-in-time** design (poll the Plex session, look ahead of the playhead, transcribe the upcoming foreign block, inject a sub) is rejected: (1) you can't cheaply know *where* the foreign dialogue is without ~transcribing to find it, so the "only do the foreign bits on the fly" saving evaporates while racing the playhead; (2) Plex won't reliably hot-load an external sub mid-stream. Move the clever part from **timing** to **segmentation**: detect the foreign segments **once at import**, transcribe/translate only those, emit a scoped forced sub.

**The cost crux (why this is opt-in):** a file's track-level audio language reads "English"; a 2-minute foreign scene inside that same track is invisible to metadata, chapters, and the skip-English optimization. To *find* the scene you must examine the audio of every English file — which defeats the skip-English logic that exists to avoid touching those files. So this is **opt-in and gated**, never on by default.

## Architecture boundary (locked)

**subarr owns the detection intelligence, gating, output, and coverage integration. subgen stays a thin primitive.** Critically, the primitives subarr needs **already exist** on subgen — no fork changes required for slice 1:

- **subgen `/detect_language_robust`** (used in #357) — language-ID of an uploaded audio clip. subarr uses it for tier-1 LID.
- **subgen `/asr` with `task=translate`** (used by the arena) — transcribe+translate an uploaded audio clip to English. subarr uses it to transcribe flagged foreign spans.

subarr does everything else, all locally: **silero VAD** (already shipped in the `[vad]` extra) to segment speech, **ffmpeg** (already in the image) to clip audio spans, the LID orchestration, span assembly, output writing, gating, and the walker/at-import triggers. The only *new* dependency arrives in tier-2 (a local LID model, in subarr), and even that never touches the fork.

**Feasibility gate (first task of the plan, before building on it):** confirm subgen's `/detect_language_robust` and `/asr` accept an **uploaded audio clip** (not only a server-visible path) — the arena already uploads clips to `/asr`, which is strong evidence, but this must be verified against the running subgen, since subgen may live on a separate LAN host with no shared filesystem. If a needed endpoint is path-only, the fallback is a small, upstream-friendly subgen change (accept an upload) — but the goal is zero fork change, and the arena path suggests we already have it. (Note: `/asr task=translate` also returns the detected source language, so the transcribe step doubles as a confirmation of a span's foreign-ness.)

## The detector (the correctness core)

A forced-scene detector that misses scenes is worse than useless, so the design examines **100% of the dialogue** rather than sampling — the VAD-first step is what makes full coverage affordable (most of a movie's runtime is silence/music/score, which we skip).

1. **VAD-segment the speech (subarr, always).** silero VAD cuts the audio into speech **utterances** (natural units bounded by pauses). We classify utterances, not arbitrary fixed windows — a short foreign line is almost always its own utterance, so it is judged on its own merits, never diluted by the English around it and never falling in a gap between samples.
2. **LID every utterance.**
   - **Tier-1 (slice 1, no new model):** subarr ffmpeg-clips each speech utterance and calls subgen `/detect_language_robust` on it; assembles contiguous non-English utterances into spans. Expensive-ish (per-utterance subgen round-trips), acceptable because opt-in.
   - **Tier-2 (follow-on, pure subarr):** replace the per-utterance subgen calls with a **local spoken-LID model** (VoxLingua107 / SpeechBrain-class ONNX, lazy-loaded like the VAD/QE models) — a cheap full-audio pass, no subgen round-trips for detection. This upgrade is entirely in subarr; the fork is untouched.
3. **Overlap for long utterances.** A long continuous utterance that straddles a language switch mid-stream is tiled with **overlapping** windows (≈50% stride) so a boundary is never hidden at a window edge.
4. **Coarse-to-fine, biased to completeness.** Wherever the first pass flags foreign *or* returns low confidence, a finer overlapping re-scan pins the exact span boundaries. The refine pass fires only where something is suspected, so cost stays bounded — but because this is opt-in, the thresholds **bias toward over-flagging**: a false positive costs a few seconds of GPU and maybe a spurious cue; a false negative loses the scene the user turned this on for.

**Honest residual limit:** spoken-LID needs ~2-3 s to be confident. A sub-2 s foreign snippet (a single word) is genuinely hard to classify *and* falls below the min-duration output floor, so nothing we would have emitted is lost. A bilingual sentence with no pause between languages is inherently ambiguous — an accepted rare edge.

All granularity/confidence/bias values (min utterance seconds for LID, window/stride, confidence floor, over-flag threshold) are **named, tunable parameters**, not magic numbers.

## The gate (which files even qualify — cheap filters, before any audio pass)

- Audio track tagged **English** (slice 1's primary-language assumption; the skip-English candidates).
- **Not** a #357 multilingual / `zxx` file (those are whole-file-mixed — a different feature).
- **No** existing forced/foreign English sub (sidecar `*.forced.en.srt` or an embedded forced-English track) — don't redo work, don't clobber.
- A runtime/size floor (skip trivially short clips).

Only files passing all cheap filters reach the expensive detection pass.

## Trigger (both, one enable toggle, off by default)

A single setting — **"Deep-scan English files for foreign scenes"** — drives two entry points:
- **Manual/scheduled walker.** A "Foreign-scene deep scan" library walker reusing the **audio-audit walker pattern**: POST-triggered, GPU-polite (yields to live sweeps), throttled, resumable, per-library scope, and supervised on the Health roster (#157). This is the primary, user-controlled path.
- **At-import hook.** In the completion flow, when a newly-completed file passes the gate and the feature is enabled, it is queued into the same detection→output pipeline. "Set and forget" for people who opt in.

Both feed one pipeline; the enable toggle gates both. Off by default keeps the skip-English optimization intact for everyone else.

## Output hygiene

- Emit one `<basename>.forced.en.srt` covering all foreign spans in the file, cues offset to **absolute file time**, forced-flagged (Bazarr/Plex recognise the `.forced` naming).
- **Min-duration floor** (config): skip a span shorter than ~2-3 s.
- **Merge spans** separated by a small gap so one conversation is not fragmented into many cues/files; it is *one* sidecar.
- **Path-containment (#13):** write next to the video via the canonical→fs resolver, never outside the media root.
- **Never clobber:** if a `.forced.en.srt` or embedded forced-English track already exists, skip (also enforced by the gate).

## Idempotence

A scan-result cache keyed on **(canonical_path, mtime, size)** records "scanned → N spans / none," mirroring the probe cache. The walker and the at-import hook both consult it, so an unchanged file is never re-scanned and GPU is never re-burned. A changed file (new mtime) re-scans.

## Bail-out — "mostly foreign"

If LID flags more than a threshold fraction of speech as non-English, this is **not** a forced-segment case (it is a full-transcription / wrong-audio-language situation, e.g. the whole film is foreign and mistagged English). Emit nothing, record the result, and (optionally) surface it as a suspect-audio-language signal rather than producing a giant "forced" sub that is really the whole movie. This keeps the feature honest about what "forced" means.

## Surfacing (keep light)

- The walker exposes its own progress/state like the audio-audit walker (files scanned, spans found, generated), and is on the Health roster.
- Generated `.forced.en.srt` sidecars show up in Coverage as normal sidecars.
- An Aftercare-style note per generated file ("forced sub generated — 3 foreign scenes, 0:14 total") so results are visible without a heavy new surface.

No new full-page UI in slice 1; a Settings toggle + the walker control + the Aftercare note.

## Data flow

```
enable toggle ON
  ├─ manual walker (per-library)  ─┐
  └─ at-import (completion hook)  ─┘→ gate (English tag, not #357-multi, no existing forced, floor)
       → scan cache hit? skip
       → subarr: silero VAD → utterances
          → LID each utterance   [tier-1: subgen /detect_language_robust on ffmpeg clips]
                                  [tier-2: local LID model, no subgen round-trips]
          → over-flag + coarse-to-fine refine → foreign spans (merged, min-duration)
       → mostly-foreign? bail + record (suspect-audio-lang signal)
       → subarr ffmpeg-clip each foreign span → subgen /asr task=translate → English cues
       → assemble one <basename>.forced.en.srt (absolute timing, forced-flag, path-contained, no-clobber)
       → record scan result (cache) + Aftercare note
```

## Testing

- **Detector (pure, synthetic):** VAD-utterance list + a stub LID → assert contiguous non-English utterances assemble into the right spans; overlap catches a mid-utterance switch; the over-flag bias and confidence floor behave; sub-floor snippets are dropped; a mostly-foreign file bails.
- **Span → SRT:** spans + translated cues → correct absolute-timed, merged, forced-flagged `.forced.en.srt`; min-duration + merge-gap honoured.
- **Gate:** English-tagged non-multilingual no-existing-forced file passes; #357-multilingual, foreign-tagged, and already-forced files are excluded.
- **Idempotence:** unchanged (path, mtime, size) is skipped via the cache; a changed mtime re-scans.
- **Orchestration (stubbed subgen):** a fake subgen (`/detect_language_robust`, `/asr`) drives the pipeline end-to-end without a real Whisper; the ffmpeg clip step is injectable/mocked.
- **Walker + at-import:** the walker reports success/failure to task_health (#157) like the audio-audit one; the at-import hook only queues gated+enabled files; best-effort (never blocks completion).
- **Regression:** feature OFF (default) = zero behaviour change; skip-English path untouched.

## Acceptance

1. With the feature enabled, a mostly-English file with a genuine foreign scene gets a correctly-timed `<basename>.forced.en.srt` covering just that scene, forced-flagged, not clobbering any existing forced track.
2. Detection examines **all** speech (VAD-utterance level), not a sparse sample — a short foreign scene is not missed.
3. A mostly-foreign or mistagged file bails out (no giant "forced" sub) and is recorded.
4. Feature OFF by default = byte-for-byte today's behaviour; skip-English intact.
5. Unchanged files are never re-scanned (cache); the walker is Health-supervised and GPU-polite.
6. Full suite green; ruff clean; no bundle drift.

## Slicing (design-whole-epic → ship incrementally)

- **Slice 1 — the pipeline, tier-1 detector, both triggers, English-primary.** VAD→utterance-LID via subgen `/detect_language_robust`→spans→subgen `/asr` translate→`.forced.en.srt`; gate; scan cache; manual walker + at-import; bail-out; Aftercare note; Settings toggle. No subgen fork changes. **This is the first shippable, demand-validated feature.**
- **Slice 2 — the cheap local LID (tier-2).** Replace per-utterance subgen LID calls with a local VoxLingua/SpeechBrain-class ONNX model (lazy-loaded like `[vad]`/`[qe]`). Pure subarr; makes library-scale scanning affordable. Ships as a `[lid]` extra + baked model, opt-in.
- **Slice 3 — generalise beyond English-primary.** The pipeline is already language-agnostic; only the gate hardcodes English. Let "primary language" be the file's real audio language (via the #357 audio-language model), so "forced = anything not the primary" works for non-English users.

## Out of scope

- Just-in-time / during-playback generation (rejected above).
- Per-track detection as a *subgen mode* (subgen #17) — we use subgen's existing per-clip primitives instead; a native subgen mode is an optional later optimisation, not required.
- Burning subtitles / re-muxing; we emit a sidecar only.
- Non-`.srt` output formats.

## Risk tier

**Tier-2** — new GPU-spending pipeline (opt-in), writeback (new sidecar files under the media root), and audio-ML orchestration. Load-bearing care points: **detection completeness** (the VAD-utterance-level coverage is the whole value; a regression to sampling silently loses scenes), **path-containment + no-clobber** on the writeback, **idempotence** (never re-burn GPU), and **OFF-by-default** (the skip-English optimisation must be untouched for non-opt-in users). Multi-lens pre-merge review per the subarr review program; the writeback + detection-completeness are the spots to scrutinise; ultra at release.
