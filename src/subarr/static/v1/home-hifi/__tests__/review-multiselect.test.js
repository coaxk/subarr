// #357 — the review multi-select payload builder + glance-lane predicate.
import { describe, it, expect } from 'vitest';
import {
  buildVerifyBody, isAutoMultilingualRow, sortPendingRows, acceptMultilingualBody,
  bulkAssignConfirmText,
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

  it('does not mutate the input array (fresh reference, order preserved)', () => {
    // input's natural order WOULD be reordered by the sort, so a mutating impl
    // is detectable here.
    const rows = [{ flag: 'multilingual', id: 1 }, { flag: 'suspect', id: 2 }];
    const copy = rows.map((r) => ({ ...r }));
    const out = sortPendingRows(rows);
    expect(out).not.toBe(rows);          // new array reference
    expect(rows).toEqual(copy);           // input untouched (still multi-first)
    expect(out.map((r) => r.id)).toEqual([2, 1]);  // output sank multilingual last
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

// #457 — the confirm dialog named the single-select `bulkLang` even in
// multilingual mode, so picking en+ja prompted "Assign \"fr\"". The submission
// was right; only the prompt lied, which is worse: it is the user's last chance
// to stop a wrong bulk writeback to Sonarr and Bazarr.
describe('bulkAssignConfirmText', () => {
  it('names every selected code in multilingual mode', () => {
    const t = bulkAssignConfirmText(['en', 'ja'], 11);
    expect(t).toContain('"en", "ja"');
    expect(t).toContain('11 files');
    expect(t).toContain('audio languages');
  });

  it('never names a language that was not selected', () => {
    // The exact reported symptom: fr must not appear anywhere.
    const t = bulkAssignConfirmText(['en', 'ja'], 11);
    expect(t).not.toContain('fr');
  });

  it('single language reads naturally', () => {
    const t = bulkAssignConfirmText(['es'], 1);
    expect(t).toContain('"es"');
    expect(t).toContain('1 file?');
    expect(t).toContain('the audio language for');
  });

  it('empty selection returns null so the caller does not prompt', () => {
    expect(bulkAssignConfirmText([], 5)).toBeNull();
    expect(bulkAssignConfirmText(null, 5)).toBeNull();
    expect(bulkAssignConfirmText([null, ''], 5)).toBeNull();
  });

  it('still explains the Sonarr and Bazarr side effects', () => {
    const t = bulkAssignConfirmText(['en'], 2);
    expect(t).toContain('Sonarr');
    expect(t).toContain('Bazarr');
  });
});

// The original defect was not in either helper -- it was the CALLER handing the
// dialog a different value than it handed the submitter. applyBulk now derives
// both from one `assignCodes` array, which a unit test cannot reach directly.
// This is the next best guard: for any selection, the text names exactly the
// codes the body would submit, so a future drift between them fails here.
describe('confirm text agrees with the submitted body', () => {
  const cases = [['en'], ['en', 'ja'], ['es', 'pt', 'fr']];
  it.each(cases)('codes %j', (...codes) => {
    const list = codes.flat();
    const body = buildVerifyBody('/m/x.mkv', list);
    const text = bulkAssignConfirmText(list, 1);
    const submitted = body.lang_codes || [body.lang_code];
    for (const c of submitted) expect(text).toContain(`"${c}"`);
    // and nothing else: count the quoted codes in the text
    expect((text.match(/"[a-z]{2,3}"/g) || []).length).toBe(submitted.length);
  });
});
