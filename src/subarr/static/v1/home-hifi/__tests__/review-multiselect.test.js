// #357 — the review multi-select payload builder + glance-lane predicate.
import { describe, it, expect } from 'vitest';
import { buildVerifyBody, isAutoMultilingualRow } from '../review.jsx';

describe('buildVerifyBody', () => {
  it('two or more langs -> multi with lang_codes', () => {
    expect(buildVerifyBody('Movies/TheBeasts.mkv', ['gl', 'es', 'fr'])).toEqual({
      canonical_path: 'Movies/TheBeasts.mkv', lang_code: 'gl', source: 'user',
      lang_class: 'multi', lang_codes: ['gl', 'es', 'fr'],
    });
  });

  it('single lang -> single', () => {
    expect(buildVerifyBody('x.mkv', ['ja'])).toEqual({
      canonical_path: 'x.mkv', lang_code: 'ja', source: 'user', lang_class: 'single',
    });
  });

  it('zxx single pick -> single zxx', () => {
    expect(buildVerifyBody('x.mkv', ['zxx']).lang_code).toBe('zxx');
  });
});

describe('isAutoMultilingualRow', () => {
  it('true for auto-high-conf-multi rows', () => {
    expect(isAutoMultilingualRow({ audio_source: 'auto-high-conf-multi' })).toBe(true);
  });
  it('true for the coverage display state', () => {
    expect(isAutoMultilingualRow({ audio_source: 'multilingual' })).toBe(true);
  });
  it('false for a plain ffprobe row', () => {
    expect(isAutoMultilingualRow({ audio_source: 'ffprobe' })).toBe(false);
  });
});
