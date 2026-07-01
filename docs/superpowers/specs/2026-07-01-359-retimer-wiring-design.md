# #359 wiring slice — activate the SRT re-timer in the completion flow

**Issue:** [#359](https://github.com/coaxk/subarr/issues/359) — out-of-box subtitle quality (CPS).
**Date:** 2026-07-01
**Scope:** wire the (already-shipped, PR #401) pure `subtitle_retime.retime_srt` into the completion pipeline, behind a **default-OFF** flag. Defers: the corpus/arena tuning that proves `RetimeParams` + flips the default to ON, per-language params, and the subgen/Path-C bake.

## Context

PR #401 shipped `src/subarr/subtitle_retime.py` — a pure, #171-immune SRT→SRT re-timer (`retime_srt(text, RetimeParams()) -> str`): extend over-CPS cues into the gap before the next cue, pad micro-cues, clamped to gap/`max_cue_ms`, idempotent, never shortens or creates overlaps, no-op on comfortable subs. It is currently imported by nothing — it proves the lever but doesn't act on real subtitles. This slice activates it.

The completion pipeline: subgen writes the `.srt` sidecar → `CompletionWatcher` detects completion → `complete_entry(entry)` runs `mark_completed → _run_aftercare (readability eval) → _try_upload_to_bazarr → _maybe_plex_partial_scan`. `_run_aftercare` already locates the sidecar via `_find_srt_sidecar(canonical_path)` and reads it.

## Decision: default-OFF flag, insert before aftercare

**Default OFF.** #359's method is "off-app, prove it, then bake." The `RetimeParams` defaults are placeholders, not yet arena-proven — so shipping them on-by-default would push unproven params to every install and silently modify everyone's subtitles. Instead: wire behind `SUBARR_RETIME_ENABLED` (default off); the **next (tuning) slice** proves the params and flips the default to ON. This sequences cleanly: wire (opt-in) → prove params → bake-on.

**Insert before aftercare.** Re-time the on-disk sidecar *before* `_run_aftercare`, so aftercare measures the improved sub (the issue's "Aftercare becomes a residual CPS monitor") and the subsequent Bazarr upload sends the improved sub — no second write path.

**No backup, write-only-if-changed.** The transform is provably safe (extend-end-only, idempotent, guaranteed no new overlaps, no-op on comfortable cues), and the sub is regenerable — a `.bak` would just clutter the media dir. Write back only when the text actually changes, so unchanged/comfortable subs never touch mtime (avoids re-triggering watchers) and re-runs are no-ops.

## Components

### 1. Config — `src/subarr/config.py`
New `retime_enabled: bool` on the settings dataclass, parsed exactly like `sonarr_propagate_audio_lang`:
- field declaration `retime_enabled: bool`
- constructor: `retime_enabled=os.environ.get("SUBARR_RETIME_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")` (match the existing bool-parse idiom in that file)
- add `"retime_enabled": "SUBARR_RETIME_ENABLED"` to the env-name settings map (the dict near line 469).

### 2. `CompletionWatcher._run_retime(entry)` — `src/subarr/completion_watcher.py`
A best-effort method mirroring `_run_aftercare`'s structure:
```
def _run_retime(self, entry) -> None:
    if not settings.retime_enabled:
        return
    try:
        srt_path = self._find_srt_sidecar(entry.canonical_path)
        if not srt_path:
            return
        text = Path(srt_path).read_text(encoding="utf-8", errors="replace")
        new_text = retime_srt(text)   # RetimeParams() defaults
        if new_text != text:
            Path(srt_path).write_text(new_text, encoding="utf-8")
            log.info("re-timed %s (%s)", entry.canonical_path, srt_path)
    except Exception as e:  # noqa: BLE001 — re-timing must never break completion
        log.warning("re-time failed for %s: %s", getattr(entry, "canonical_path", "?"), e)
```
Imports: `from .subtitle_retime import retime_srt`; `settings` (already imported in the module, or add). `Path`/`log` already present.

### 3. Wire into `complete_entry`
Insert `self._run_retime(entry)` between `self._provenance.mark_completed(entry.id)` and `self._run_aftercare(entry)`.

## Data flow

```
subgen writes <base>.en.srt
  → CompletionWatcher.complete_entry(entry)
      mark_completed
      _run_retime(entry):  flag ON? → read sidecar → retime_srt → write back IF changed
      _run_aftercare(entry): reads the (now re-timed) sidecar → readability eval
      _try_upload_to_bazarr(entry): uploads the (re-timed) sidecar
      _maybe_plex_partial_scan
```

## Error handling

- Flag off → immediate return (zero cost, zero disk touch).
- Any exception (missing sidecar, parse, IO, permissions) → logged warning, completion proceeds unchanged. Re-timing never blocks the loop (same contract as `_run_aftercare`).
- Idempotent across `complete_entry`'s repeated invocations (polling + webhook #87): a re-timed sub re-times to itself → `new_text == text` → no write.

## Testing

Unit tests against a `CompletionWatcher` with a real temp `.srt` sidecar (or a stubbed `_find_srt_sidecar`):
- **flag OFF** → `_run_retime` leaves the sidecar bytes byte-for-byte unchanged.
- **flag ON + high-CPS sidecar** → sidecar rewritten; `analyze_srt(after)` shows fewer CPS-critical cues than before; still parses; no new overlaps.
- **flag ON + comfortable sidecar** → not rewritten (mtime/bytes unchanged; write-only-if-changed).
- **re-timer raises** (monkeypatch `retime_srt` to throw) → `_run_retime` swallows it; a full `complete_entry` still completes (aftercare + upload still run).
- **ordering** → after `complete_entry`, the sidecar aftercare recorded / bazarr uploaded is the re-timed content (assert via the sidecar on disk being re-timed before those steps read it).
- **config** → `SUBARR_RETIME_ENABLED` unset → `settings.retime_enabled is False`; `="1"` → `True`.

## Acceptance

1. With `SUBARR_RETIME_ENABLED` unset/off, completion behaviour is byte-for-byte identical to today (no sidecar writes, no new cost).
2. With it on, a high-CPS sidecar is re-timed in place before aftercare/upload, CPS measurably reduced, no new overlaps, comfortable subs untouched.
3. A re-timer failure never blocks completion.
4. Full suite green; ruff clean.

## Out of scope (follow-ons)

- The corpus/arena tuning that proves `RetimeParams` and **flips the default to ON**.
- Per-language `RetimeParams`, env param-overrides (the arena tunes off-app by passing params directly — not needed in the deployed path yet).
- The bake (subgen default / Path C upstream).
- Any UI surface (this is invisible pipeline plumbing; the arena stays the power-user surface).

## Risk tier

**Tier-1** — modifies subtitle output on disk in the completion path, but behind a default-off flag, best-effort (never blocks completion), and the transform is pure + proven (PR #401). Multi-lens read-diff review; no auth/data-model/cross-service surface.
