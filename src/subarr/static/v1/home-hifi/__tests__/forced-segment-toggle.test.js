// #364 — forced-segment Settings toggle: pure render-decision helper.
import { describe, it, expect } from 'vitest';
import { deriveForcedSegmentToggle } from '../settings.jsx';

describe('deriveForcedSegmentToggle', () => {
  it('env-pinned -> disabled with env hint', () => {
    const v = deriveForcedSegmentToggle({ enabled: true, env_controlled: true, vad_available: true });
    expect(v.checked).toBe(true);
    expect(v.disabled).toBe(true);
    expect(v.hint).toMatch(/SUBARR_FORCED_SEGMENT_ENABLED/);
  });
  it('not pinned -> enabled, persist hint', () => {
    const v = deriveForcedSegmentToggle({ enabled: false, env_controlled: false, vad_available: true });
    expect(v.checked).toBe(false);
    expect(v.disabled).toBe(false);
    expect(v.hint).toMatch(/Persists across restarts/);
    expect(v.warning).toBeNull();
  });
  it('enabled but VAD model missing -> warning (warn, not block)', () => {
    const v = deriveForcedSegmentToggle({ enabled: true, env_controlled: false, vad_available: false });
    expect(v.disabled).toBe(false);
    expect(v.warning).toMatch(/speech-detection/i);
  });
  it('off + VAD missing -> no warning yet', () => {
    const v = deriveForcedSegmentToggle({ enabled: false, env_controlled: false, vad_available: false });
    expect(v.warning).toBeNull();
  });
  it('null status -> safe defaults', () => {
    const v = deriveForcedSegmentToggle(null);
    expect(v.checked).toBe(false);
    expect(v.disabled).toBe(false);
  });
});
