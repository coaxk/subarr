// Phase A subtitle-tuning Settings panel: pure helper tests.
// The panel delegates its decisions to the exported pure helpers below
// (same pattern as deriveForcedSegmentToggle), so these exercise the real
// deep-link, rail-activation, save/error, preview-overrides and playback
// logic without a DOM.
import { describe, it, expect } from 'vitest';
import {
  TUNING_DEFAULTS,
  TUNING_BOUNDS,
  RETIMER_FIELDS,
  SETTINGS_VIEW_WHITELIST,
  fieldIsEnv,
  fieldCurrentValue,
  editableKeysFor,
  fieldDraftValue,
  fieldsDirty,
  parseTuningNum,
  tuningSaveBody,
  tuningResetRequest,
  flattenErrorDetail,
  buildPreviewBody,
  tuningOverridesFor,
  wpmForCps,
  parseSrtCues,
  cueIndexAt,
  nextPlaybackElapsed,
  previewSeqIsStale,
  previewAbortIgnored,
  resolveSettingsView,
} from '../settings.jsx';

// Shared fixture: every field editable at its backend default.
const allEditable = Object.fromEntries(
  Object.keys(TUNING_DEFAULTS).map((k) => [k, { value: TUNING_DEFAULTS[k], env_controlled: false }]),
);

// ── hash deep-link selection + rail activation ─────────────────────────────
describe('resolveSettingsView (deep-link)', () => {
  it('#subtitle-tuning selects the subtitle-tuning view', () => {
    expect(resolveSettingsView('#subtitle-tuning')).toEqual({ view: 'subtitle-tuning' });
  });
  it('is case-insensitive and tolerates a missing #', () => {
    expect(resolveSettingsView('SUBTITLE-TUNING')).toEqual({ view: 'subtitle-tuning' });
    expect(resolveSettingsView('#SUBGEN-TUNING')).toEqual({ view: 'subgen-tuning' });
  });
  it('maps the summary + integration deep-links', () => {
    expect(resolveSettingsView('#integrations')).toEqual({ view: 'integrations-summary' });
    expect(resolveSettingsView('#integration:bazarr')).toEqual({ view: 'integration', selectedId: 'bazarr' });
  });
  it('known views pass through, unknown / empty resolve to null', () => {
    expect(resolveSettingsView('#providers')).toEqual({ view: 'providers' });
    expect(resolveSettingsView('#lang-rules')).toEqual({ view: 'lang-rules' });
    expect(resolveSettingsView('#no-such-view')).toBeNull();
    expect(resolveSettingsView('')).toBeNull();
  });
});

describe('rail activation + position', () => {
  it('subtitle-tuning is a whitelisted view (activates its rail item + panel)', () => {
    expect(SETTINGS_VIEW_WHITELIST).toContain('subtitle-tuning');
    // the deep-link view is the same value the rail/panel compare against
    expect(resolveSettingsView('#subtitle-tuning').view).toBe('subtitle-tuning');
  });
  it('sits between lang-rules and subgen-tuning in the rail order', () => {
    const i = SETTINGS_VIEW_WHITELIST.indexOf('subtitle-tuning');
    expect(SETTINGS_VIEW_WHITELIST.indexOf('lang-rules')).toBeLessThan(i);
    expect(SETTINGS_VIEW_WHITELIST.indexOf('subgen-tuning')).toBeGreaterThan(i);
  });
});

// ── control rendering / locking ────────────────────────────────────────────
describe('control rendering & env locking', () => {
  it('fieldIsEnv flags env-managed fields', () => {
    expect(fieldIsEnv({ retime_enabled: { value: true, env_controlled: true } }, 'retime_enabled')).toBe(true);
    expect(fieldIsEnv({ retime_enabled: { value: true, env_controlled: false } }, 'retime_enabled')).toBe(false);
    expect(fieldIsEnv({}, 'retime_enabled')).toBe(false);
    expect(fieldIsEnv(null, 'retime_enabled')).toBe(false);
  });
  it('editableKeysFor excludes env-managed fields; empty when all env-locked', () => {
    const allEditableKeys = editableKeysFor({ target_cps: { value: 17, env_controlled: false } });
    expect(allEditableKeys).toEqual(Object.keys(TUNING_DEFAULTS));
    const onePinned = editableKeysFor({ target_cps: { value: 17, env_controlled: true } });
    expect(onePinned).not.toContain('target_cps');
    expect(onePinned).toHaveLength(Object.keys(TUNING_DEFAULTS).length - 1);
    const allPinned = Object.fromEntries(Object.keys(TUNING_DEFAULTS).map((k) => [k, { value: 1, env_controlled: true }]));
    expect(editableKeysFor(allPinned)).toEqual([]); // envLocked == true
  });
  it('fieldCurrentValue falls back to compile-time defaults when no backend data', () => {
    expect(fieldCurrentValue(null, 'target_cps')).toBe(17.0);
    expect(fieldCurrentValue({}, 'min_cue_ms')).toBe(1000);
    expect(fieldCurrentValue({ target_cps: { value: 22.5, env_controlled: false } }, 'target_cps')).toBe(22.5);
  });
  it('RETIMER_FIELDS mirrors the four ms knobs (target_cps is the top-level reading speed)', () => {
    const keys = RETIMER_FIELDS.map((f) => f.key);
    expect(keys).toEqual(['min_cue_ms', 'min_gap_ms', 'max_cue_ms', 'max_borrow_ms']);
    for (const k of keys) expect(k in TUNING_DEFAULTS).toBe(true);
    for (const f of RETIMER_FIELDS) {
      expect(f.label).toBeTruthy();
      // min/max/step come from the single TUNING_BOUNDS source, not the field
      expect(TUNING_BOUNDS[f.key]).toBeTruthy();
      expect(typeof TUNING_BOUNDS[f.key].step).toBe('number');
    }
  });
  it('TUNING_DEFAULTS carries the exact backend defaults (no validate_language)', () => {
    expect(TUNING_DEFAULTS).toEqual({
      retime_enabled: true, target_cps: 17.0,
      min_cue_ms: 1000, min_gap_ms: 100, max_cue_ms: 7000, max_borrow_ms: 500,
    });
    expect('validate_language' in TUNING_DEFAULTS).toBe(false);
  });
  it('TUNING_BOUNDS mirrors the backend Canonical Bounds exactly', () => {
    expect(TUNING_BOUNDS).toEqual({
      target_cps:    { min: 5,    max: 25,    step: 0.5 },
      min_cue_ms:    { min: 100,  max: 5000,  step: 100 },
      min_gap_ms:    { min: 0,    max: 1000,  step: 10 },
      max_cue_ms:    { min: 1000, max: 15000, step: 100 },
      max_borrow_ms: { min: 0,    max: 5000,  step: 50 },
    });
  });
  it('wpmForCps maps CPS to WPM (WPM = round(cps * 10))', () => {
    expect(wpmForCps(17)).toBe(170);
    expect(wpmForCps(12.5)).toBe(125);
    expect(wpmForCps(5)).toBe(50);
    expect(wpmForCps(25)).toBe(250);
    expect(wpmForCps('17.5')).toBe(175); // string drafts from the number input
  });
});

// ── save / error states ────────────────────────────────────────────────────
describe('save body + 422 error surfacing', () => {
  it('empty draft -> empty body (nothing to save)', () => {
    expect(tuningSaveBody(allEditable, {})).toEqual({});
  });
  it('bools coerced to !! and numerics parsed, unchanged fields skipped', () => {
    const body = tuningSaveBody(allEditable, { retime_enabled: false, target_cps: '22.5', min_cue_ms: '1200' });
    expect(body).toEqual({ retime_enabled: false, target_cps: 22.5, min_cue_ms: 1200 });
    // min_gap_ms unchanged -> skipped
    expect('min_gap_ms' in body).toBe(false);
  });
  it('numeric parse: target_cps float, others int, blank/null dropped', () => {
    expect(parseTuningNum('target_cps', { target_cps: '20.25' })).toBe(20.25);
    expect(parseTuningNum('min_cue_ms', { min_cue_ms: '1500' })).toBe(1500);
    expect(parseTuningNum('min_cue_ms', { min_cue_ms: '' })).toBeUndefined();
    expect(parseTuningNum('min_cue_ms', {})).toBeUndefined();
  });
  it('env-managed fields are never sent in the save body', () => {
    const pinned = { ...allEditable, target_cps: { value: 99, env_controlled: true } };
    const body = tuningSaveBody(pinned, { target_cps: '1.0', min_gap_ms: '250' });
    expect('target_cps' in body).toBe(false); // env owns it
    expect(body.min_gap_ms).toBe(250); // editable still saved
  });
  it('fieldsDirty detects pending edits across editable fields', () => {
    expect(fieldsDirty({}, allEditable)).toBe(false);
    expect(fieldsDirty({ min_gap_ms: '250' }, allEditable)).toBe(true);
  });
  it('flattenErrorDetail turns a 422 detail array into one message', () => {
    const j = { detail: [{ msg: 'target_cps must be <= 100' }, { msg: 'min_cue_ms must be >= 0' }] };
    expect(flattenErrorDetail(j)).toBe('target_cps must be <= 100; min_cue_ms must be >= 0');
    expect(flattenErrorDetail({ detail: 'Pick a sample first.' })).toBe('Pick a sample first.');
    expect(flattenErrorDetail({})).toBeNull();
    expect(flattenErrorDetail(null)).toBeNull();
  });
  it('tuningResetRequest is a bodyless DELETE (no literal-defaults payload)', () => {
    expect(tuningResetRequest()).toEqual({ method: 'DELETE' });
    // no body key -> nothing literal is PUT to the backend; clear_override
    // semantics on DELETE revert persisted overrides to built-in defaults
    expect('body' in tuningResetRequest()).toBe(false);
  });
});

// ── preview overrides (transient, never persisted) ─────────────────────────
describe('tuningOverridesFor (draft -> transient preview overrides)', () => {
  it('nothing edited -> {overrides: null, error: null}', () => {
    expect(tuningOverridesFor({}, allEditable)).toEqual({ overrides: null, error: null });
  });
  it('edited target_cps parses and is included', () => {
    expect(tuningOverridesFor({ target_cps: '12.5' }, allEditable)).toEqual({ overrides: { target_cps: 12.5 }, error: null });
  });
  it('out-of-bounds value -> friendly error naming the field', () => {
    const { overrides, error } = tuningOverridesFor({ target_cps: '30' }, allEditable);
    expect(overrides).toBeNull();
    expect(error.key).toBe('target_cps');
    expect(error.message).toBe('Target CPS must be between 5 and 25.');
  });
  it('empty / non-numeric value -> enter-a-number error', () => {
    expect(tuningOverridesFor({ target_cps: '' }, allEditable).error.message).toBe('Enter a number for Target CPS.');
    expect(tuningOverridesFor({ target_cps: 'abc' }, allEditable).error.message).toBe('Enter a number for Target CPS.');
    expect(tuningOverridesFor({ min_cue_ms: 'x2' }, allEditable).error.message).toBe('Enter a number for Min cue duration (ms).');
  });
  it('boundary values are accepted', () => {
    expect(tuningOverridesFor({ target_cps: '5' }, allEditable).overrides).toEqual({ target_cps: 5 });
    expect(tuningOverridesFor({ target_cps: '25' }, allEditable).overrides).toEqual({ target_cps: 25 });
    expect(tuningOverridesFor({ max_cue_ms: '15000' }, allEditable).overrides).toEqual({ max_cue_ms: 15000 });
  });
  it('env-pinned fields are never overridden (even when edited)', () => {
    const pinned = { ...allEditable, target_cps: { value: 17, env_controlled: true } };
    expect(tuningOverridesFor({ target_cps: '12.5', min_gap_ms: '250' }, pinned)).toEqual({
      overrides: { min_gap_ms: 250 }, error: null,
    });
  });
  it('unchanged / untouched fields are skipped', () => {
    // target_cps '17' string equals the 17.0 default -> skipped
    expect(tuningOverridesFor({ target_cps: '17', min_cue_ms: '1200' }, allEditable)).toEqual({
      overrides: { min_cue_ms: 1200 }, error: null,
    });
  });
});

// ── preview body building ──────────────────────────────────────────────────
describe('buildPreviewBody (sample/custom + overrides)', () => {
  it('sample mode sends the selected sample_id (no overrides when none)', () => {
    const { body, error } = buildPreviewBody('sample', { sampleId: 'sdh', customText: '', overrides: null });
    expect(error).toBeNull();
    expect(body).toEqual({ sample_id: 'sdh' });
  });
  it('sample mode with cleaned overrides -> body includes the overrides object', () => {
    const { body, error } = buildPreviewBody('sample', { sampleId: 'sdh', customText: '', overrides: { target_cps: 12.5, min_gap_ms: 250 } });
    expect(error).toBeNull();
    expect(body).toEqual({ sample_id: 'sdh', overrides: { target_cps: 12.5, min_gap_ms: 250 } });
  });
  it('custom mode sends the raw text and any overrides', () => {
    const text = '1\n00:00:00,000 --> 00:00:02,000\nHi';
    const { body, error } = buildPreviewBody('custom', { sampleId: 'sdh', customText: text, overrides: { target_cps: 18 } });
    expect(error).toBeNull();
    expect(body.sample_id).toBeUndefined();
    expect(body.text).toContain('00:00:02,000');
    expect(body.overrides).toEqual({ target_cps: 18 });
  });
  it('defensive: out-of-range / non-numeric overrides are dropped, never sent', () => {
    const { body, error } = buildPreviewBody('sample', { sampleId: 'sdh', overrides: { target_cps: 30, min_cue_ms: 99999, min_gap_ms: 'bogus' } });
    expect(error).toBeNull();
    expect(body.sample_id).toBe('sdh');
    expect('overrides' in body).toBe(false); // everything invalid -> no overrides key
  });
  it('defensive: mixed valid/invalid overrides keep only the valid ones', () => {
    const { body } = buildPreviewBody('sample', { sampleId: 'sdh', overrides: { target_cps: '12.5', min_gap_ms: 5000 } });
    expect(body.overrides).toEqual({ target_cps: 12.5 });
  });
  it('sample selection guard: no pick -> friendly error', () => {
    const { body, error } = buildPreviewBody('sample', { sampleId: '', customText: 'x', overrides: null });
    expect(body).toBeNull();
    expect(error).toMatch(/sample first/i);
  });
  it('custom text guard: blank input -> friendly error', () => {
    const { body, error } = buildPreviewBody('custom', { sampleId: 'sdh', customText: '   \n ', overrides: null });
    expect(body).toBeNull();
    expect(error).toMatch(/subtitle text first/i);
  });
});

// ── timed playback helpers ─────────────────────────────────────────────────
describe('parseSrtCues (SRT -> timed cues)', () => {
  it('parses a two-cue block SRT with startMs/endMs/text', () => {
    const srt = `1
00:00:00,000 --> 00:00:02,000
Hello there

2
00:00:02,500 --> 00:00:04,000
How are you
`;
    const cues = parseSrtCues(srt);
    expect(cues).toHaveLength(2);
    expect(cues[0]).toEqual({ index: 1, startMs: 0, endMs: 2000, text: 'Hello there' });
    expect(cues[1]).toEqual({ index: 2, startMs: 2500, endMs: 4000, text: 'How are you' });
  });
  it('keeps multi-line cue text joined with newlines', () => {
    const srt = `1
00:00:00,000 --> 00:00:03,000
Line one
Line two
`;
    const cues = parseSrtCues(srt);
    expect(cues[0].text).toBe('Line one\nLine two');
  });
  it('skips malformed blocks (no timestamp line, or no text)', () => {
    const srt = `1
00:00:00,000 --> 00:00:02,000
Hello

garbage block, no timestamps at all
just prose

3
00:00:05,000 --> 00:00:06,000

`;
    const cues = parseSrtCues(srt);
    expect(cues).toHaveLength(1);
    expect(cues[0].text).toBe('Hello');
  });
  it('normalises CRLF line endings', () => {
    const crlf = '1\r\n00:00:00,000 --> 00:00:01,000\r\nLine A\r\n\r\n2\r\n00:00:02,000 --> 00:00:03,000\r\nLine B\r\n';
    const cues = parseSrtCues(crlf);
    expect(cues).toHaveLength(2);
    expect(cues[0].text).toBe('Line A');
    expect(cues[1].startMs).toBe(2000);
    expect(cues[1].endMs).toBe(3000);
  });
  it('handles empty / non-SRT input as no cues', () => {
    expect(parseSrtCues('')).toEqual([]);
    expect(parseSrtCues('plain text\nmore text')).toEqual([]);
    expect(parseSrtCues(null)).toEqual([]);
  });
});

describe('cueIndexAt (elapsed position -> active cue)', () => {
  const cues = [
    { index: 1, startMs: 0, endMs: 2000, text: 'A' },
    { index: 2, startMs: 2500, endMs: 4000, text: 'B' },
  ];
  it('before the first cue -> -1', () => {
    expect(cueIndexAt(cues, -1)).toBe(-1);
  });
  it('inside cue 1 -> 0 (window is [startMs, endMs))', () => {
    expect(cueIndexAt(cues, 0)).toBe(0);
    expect(cueIndexAt(cues, 1999)).toBe(0);
    expect(cueIndexAt(cues, 2000)).toBe(-1); // exactly at end is out of the window
  });
  it('in the gap between cues -> -1', () => {
    expect(cueIndexAt(cues, 2400)).toBe(-1);
  });
  it('inside the second cue -> 1', () => {
    expect(cueIndexAt(cues, 2500)).toBe(1);
    expect(cueIndexAt(cues, 3999)).toBe(1);
  });
  it('after the last cue -> -1; empty list -> -1', () => {
    expect(cueIndexAt(cues, 4000)).toBe(-1);
    expect(cueIndexAt(cues, 99999)).toBe(-1);
    expect(cueIndexAt([], 0)).toBe(-1);
  });
});

describe('nextPlaybackElapsed (playback-clock end-of-playback clamp)', () => {
  it('before the end -> raw elapsed, done false', () => {
    expect(nextPlaybackElapsed(1000, 500, 700, 5000)).toEqual({ elapsed: 1200, done: false });
  });
  it('exactly at the end -> clamped to totalMs, done true', () => {
    expect(nextPlaybackElapsed(0, 0, 5000, 5000)).toEqual({ elapsed: 5000, done: true });
  });
  it('past the end -> clamped to totalMs, done true', () => {
    expect(nextPlaybackElapsed(2000, 1000, 9000, 5000)).toEqual({ elapsed: 5000, done: true });
  });
  it('unbounded (totalMs 0 or negative) -> raw elapsed, done false', () => {
    expect(nextPlaybackElapsed(0, 0, 1234, 0)).toEqual({ elapsed: 1234, done: false });
    expect(nextPlaybackElapsed(0, 0, 1234, -1)).toEqual({ elapsed: 1234, done: false });
  });
});

// Stale-preview guard decisions (pure): a run whose captured seq no longer
// matches the latest seq has been superseded, and an AbortError is a
// cancellation, not a failure.
describe('preview stale-guard helpers (run supersession)', () => {
  it('previewSeqIsStale: true when a newer run has superseded this one', () => {
    expect(previewSeqIsStale(1, 2)).toBe(true);
    expect(previewSeqIsStale(3, 4)).toBe(true);
  });
  it('previewSeqIsStale: false when this run is still the latest', () => {
    expect(previewSeqIsStale(2, 2)).toBe(false);
    expect(previewSeqIsStale(5, 5)).toBe(false);
  });
  it('previewAbortIgnored: an AbortError is a cancelled (ignored) request', () => {
    expect(previewAbortIgnored(new Error('Aborted'))).toBe(false);
    expect(previewAbortIgnored(Object.assign(new Error('nope'), { name: 'AbortError' }))).toBe(true);
    expect(previewAbortIgnored(null)).toBe(false);
    expect(previewAbortIgnored(undefined)).toBe(false);
  });
});
