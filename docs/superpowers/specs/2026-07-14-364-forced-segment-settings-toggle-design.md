# Forced-Segment Settings Toggle — Design

**Issue:** #364 (forced-segment epic — "finish" follow-on).
**Date:** 2026-07-14
**Status:** approved for planning.

## Goal

Make the already-shipped forced-segment feature reachable from the UI. Today it
is `SUBARR_FORCED_SEGMENT_ENABLED` env-only, so an English user who would benefit
cannot turn it on without editing their compose file. Add a Settings switch that
persists across restarts and takes effect immediately — mirroring the existing
VAD toggle exactly.

Non-goals (explicitly deferred): the slice-3 primary-language generalisation
(deferred — no non-English-primary demand); a "Scan library now" button (the
manual walker already has a `/start` API we can surface later); any change to the
detector, gate, or output.

## Context

- `forced_segment_enabled` already exists as a config field, is loaded from
  `SUBARR_FORCED_SEGMENT_ENABLED` (default `"0"`), and is registered in
  `FIELD_ENV_VARS` + the coerce map — so it is already override-capable through
  the #112 config-store layer. No config plumbing to add.
- `completion_watcher._maybe_forced_segment` reads `settings.forced_segment_enabled`
  **live** on each import, so mutating the running Settings object applies the
  change with no restart.
- The proven analog is the VAD toggle: `routers/vad.py` (`GET /api/vad/status` +
  `POST /api/vad/config`) and its `settings.jsx` component (~line 1426).

## Architecture

### Backend — `routers/forced_segment.py`

Add a small shared status helper and one new endpoint; extend the existing GET.

```python
def _toggle_status() -> dict:
    from .. import config, vad
    return {
        "enabled": bool(getattr(config.settings, "forced_segment_enabled", False)),
        "env_controlled": config.env_is_set("forced_segment_enabled"),
        "vad_available": vad.vad_available(),  # hard prerequisite; no fallback
    }
```

- **`GET /api/forced-segment`** — merge `_toggle_status()` into the current
  `{state, summary}` payload. One fetch drives both the toggle and the scan panel
  (no extra poll).
- **`POST /api/forced-segment/config {enabled: bool}`** — persist then live-apply,
  exactly like `vad.set_config`:
  ```python
  config_store.save_override("forced_segment_enabled", body.enabled)
  if not config.env_is_set("forced_segment_enabled"):
      object.__setattr__(config.settings, "forced_segment_enabled", body.enabled)
  return _toggle_status()
  ```
  Env stays authoritative: if the operator pinned the env var, we still persist the
  preference but do not mutate the live value (env wins on reload). Inherits the
  router's existing auth gate.

`vad_available` is surfaced (not gating) because VAD is a **hard** prerequisite —
without the silero model every scan records `vad-unavailable` and silently does
nothing. The local LID model is optional (subgen `/asr` is the fallback), so it is
not part of the prerequisite signal.

### Frontend — `settings.jsx`

A new section beside the VAD card, mirroring its structure:

- Fetches `GET /api/forced-segment`; renders a single switch bound to `enabled`.
- On change, `POST /api/forced-segment/config {enabled}`; re-reads status from the
  response.
- **Env-lock:** when `env_controlled`, the switch is disabled with the hint
  "Locked by SUBARR_FORCED_SEGMENT_ENABLED (env wins)"; otherwise "Persists across
  restarts".
- **Warn-but-allow prerequisite:** when `vad_available` is false, show an inline
  warning ("Requires the speech-detection (VAD) model — enable Speech-aware audio
  above first, or scans will find nothing") but still allow toggling on. Warn, do
  not block.
- Explanatory copy: deep-scans English media for short foreign-language scenes and
  writes a scoped `.forced.en.srt`; GPU-spending, opt-in.

## Data flow

```
UI switch → POST /api/forced-segment/config {enabled}
          → config_store.save_override("forced_segment_enabled", enabled)   # persist (#112)
          → object.__setattr__(settings, ...) unless env-pinned             # live-apply
          → returns {enabled, env_controlled, vad_available}
completion_watcher._maybe_forced_segment reads settings.forced_segment_enabled live → next import honours it
```

## Error handling

- Persist and live-apply follow the VAD precedent; `save_override` is already
  hardened (locked RMW, never clobber unreadable, fsync — v2.4.2). A persist
  failure surfaces as a 500 from the endpoint, same as VAD; the UI keeps the prior
  state on a failed POST.
- Env-pinned: persisted but not live-applied — documented in the hint so behaviour
  is not surprising.

## Testing

**Backend (`tests/test_forced_segment_router.py`, mirroring `test_vad_router.py`):**
- `POST /config {enabled: true}` with env unset → `save_override` called AND
  `settings.forced_segment_enabled is True` (live-applied); response reflects it.
- `POST /config` with `SUBARR_FORCED_SEGMENT_ENABLED` pinned → persisted but the
  live value is unchanged (env wins).
- `GET /api/forced-segment` returns `enabled`, `env_controlled`, `vad_available`
  alongside the existing `state`/`summary`.
- `vad_available` reflects `vad.vad_available()` (monkeypatched both ways).

**Frontend (`__tests__/forced-segment-toggle.test.js`, mirroring the pure-helper
convention, e.g. `review-multiselect.test.js`):** extract the render-decision logic
into a pure helper and unit-test it — the codebase tests pure helpers, not DOM
renders.

```js
// deriveForcedSegmentToggle(status) -> { checked, disabled, hint, warning }
```
- `env_controlled: true` → `disabled: true`, hint "Locked by SUBARR_FORCED_SEGMENT_ENABLED (env wins)".
- `env_controlled: false` → `disabled: false`, hint "Persists across restarts".
- `enabled: true, vad_available: false` → `warning` set (the prerequisite notice).
- `enabled` maps straight to `checked`.

The component wires this helper to the switch and the `POST /config` handler; the
backend contract tests cover the persistence/apply behaviour.

**Bundle:** rebuild `settings.bundle.js` (esbuild) so the bundle-drift CI check
passes.

**Regression:** feature OFF by default unchanged; no behaviour change unless the
user toggles it on.

## Acceptance criteria

1. A user can enable/disable forced-segment from Settings without editing compose;
   the choice survives a restart.
2. Toggling on takes effect on the next import with no restart.
3. When `SUBARR_FORCED_SEGMENT_ENABLED` is pinned, the switch is locked and env
   remains authoritative.
4. When the VAD model is absent, the UI warns but still lets the user toggle on.
5. Default OFF, byte-for-byte today's behaviour until toggled.
6. Full suite + vitest + bundle-drift green.
