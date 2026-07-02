// #357 — the review multi-select payload builder + glance-lane predicate.
import { describe, it, expect } from 'vitest';
import {
  buildVerifyBody, isAutoMultilingualRow, sortPendingRows, acceptMultilingualBody,
} from '../review.jsx';

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

  it('empty selection -> null (caller must skip)', () => {
    expect(buildVerifyBody('x.mkv', [])).toBe(null);
    expect(buildVerifyBody('x.mkv', [null, ''])).toBe(null);
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

describe('sortPendingRows', () => {
  it('orders multilingual rows last, stable otherwise', () => {
    const rows = [
      { canonical_path: 'a', flag: 'suspect' },
      { canonical_path: 'b', flag: 'multilingual' },
      { canonical_path: 'c', flag: 'unknown' },
      { canonical_path: 'd', flag: 'multilingual' },
      { canonical_path: 'e', flag: 'track_mismatch' },
    ];
    const out = sortPendingRows(rows).map((r) => r.canonical_path);
    // non-multilingual keep original relative order; multilingual sink to the end.
    expect(out).toEqual(['a', 'c', 'e', 'b', 'd']);
  });

  it('does not mutate the input array', () => {
    const rows = [{ flag: 'multilingual' }, { flag: 'suspect' }];
    const copy = rows.slice();
    sortPendingRows(rows);
    expect(rows).toEqual(copy);
  });
});

describe('acceptMultilingualBody', () => {
  it('builds a user multi body from the row own lang_codes', () => {
    expect(acceptMultilingualBody({
      canonical_path: 'Movies/TheBeasts.mkv', lang_codes: ['gl', 'es', 'fr'],
    })).toEqual({
      canonical_path: 'Movies/TheBeasts.mkv', lang_code: 'gl', source: 'user',
      lang_class: 'multi', lang_codes: ['gl', 'es', 'fr'],
    });
  });

  it('prefers file_canonical_path when present', () => {
    expect(acceptMultilingualBody({
      file_canonical_path: 'F.mkv', canonical_path: 'C.mkv', lang_codes: ['es', 'en'],
    }).canonical_path).toBe('F.mkv');
  });

  it('returns null for a row missing lang_codes (caller skips)', () => {
    expect(acceptMultilingualBody({ canonical_path: 'x.mkv' })).toBe(null);
    expect(acceptMultilingualBody({ canonical_path: 'x.mkv', lang_codes: [] })).toBe(null);
  });
});
