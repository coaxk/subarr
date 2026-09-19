// #569: one list decides which skips are a correct outcome (neutral chip,
// Recently done) rather than an Issue. It used to be two copies inside
// queue.jsx; `audio_lang_expected` (the user verified a language this subgen
// is set to skip) joins it.
import { describe, it, expect } from 'vitest';
import { BENIGN_SKIP_REASONS, isBenignSkip } from '../queue.jsx';

describe('isBenignSkip', () => {
  it('treats the expected audio-language skip as benign', () => {
    expect(isBenignSkip({ category: 'skipped', skip_reason: 'audio_lang_expected' })).toBe(true);
  });

  it('keeps the existing benign reasons', () => {
    for (const r of ['sub_exists', 'file_removed', 'interrupted']) {
      expect(isBenignSkip({ skip_reason: r })).toBe(true);
    }
  });

  it('leaves actionable skips in Issues', () => {
    expect(isBenignSkip({ skip_reason: 'audio_lang' })).toBe(false);
    expect(isBenignSkip({ skip_reason: 'unknown' })).toBe(false);
    expect(isBenignSkip({})).toBe(false);
    expect(isBenignSkip(undefined)).toBe(false);
  });

  it('is exactly this list', () => {
    expect([...BENIGN_SKIP_REASONS].sort()).toEqual(
      ['audio_lang_expected', 'file_removed', 'interrupted', 'sub_exists']);
  });
});
