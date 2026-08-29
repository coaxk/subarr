// #458 follow-on: bulk "transcribe anyway" for image-only rows.
//
// These rows have English audio AND an English subtitle, so every other path
// through subgen refuses them. bypass_skip is the only thing that reaches
// them, which makes the flag load-bearing rather than an optimisation: drop it
// and the whole action silently does nothing.
import { describe, it, expect } from 'vitest';
import { coverageQueueBody, imageOnlyRows } from '../coverage.jsx';

describe('coverageQueueBody bypass_skip', () => {
  it('omits bypass_skip by default', () => {
    const body = coverageQueueBody({ _canonical_path: 'TV/x.mkv' });
    expect(body.bypass_skip).toBeUndefined();
  });

  it('sends bypass_skip when asked', () => {
    const body = coverageQueueBody({ _canonical_path: 'TV/x.mkv' }, { bypassSkip: true });
    expect(body.bypass_skip).toBe(true);
  });

  it('is independent of ignoreForced', () => {
    // Different defects: forced is a partial transcript, image is not text at
    // all. A row can need one, the other, or both.
    const body = coverageQueueBody(
      { _canonical_path: 'TV/x.mkv' },
      { bypassSkip: true, ignoreForced: true },
    );
    expect(body.bypass_skip).toBe(true);
    expect(body.ignore_forced).toBe(true);
  });

  it('still routes episodes by sonarr id', () => {
    const body = coverageQueueBody(
      { _sonarr_episode_id: 77, _canonical_path: 'TV/x.mkv' },
      { bypassSkip: true },
    );
    expect(body.sonarr_episode_id).toBe(77);
    expect(body.canonical_path).toBeUndefined();
    expect(body.bypass_skip).toBe(true);
  });
});

describe('imageOnlyRows', () => {
  const rows = [
    { title: 'a', embedded_en: 'EN(image)' },
    { title: 'b', embedded_en: 'EN' },
    { title: 'c', embedded_en: 'EN(forced)' },
    { title: 'd', embedded_en: null },
    { title: 'e', embedded_en: 'EN(image)' },
    { title: 'f', embedded_en: 'EN(SDH)' },
  ];

  it('selects only image-only rows', () => {
    expect(imageOnlyRows(rows).map(r => r.title)).toEqual(['a', 'e']);
  });

  it('does not treat forced as image', () => {
    // Both are "partial coverage" and both are demoted, but only image rows
    // need bypass_skip -- forced has its own narrower override.
    expect(imageOnlyRows(rows).some(r => r.embedded_en === 'EN(forced)')).toBe(false);
  });

  it('is empty when nothing is image-only', () => {
    expect(imageOnlyRows([{ embedded_en: 'EN' }])).toEqual([]);
    expect(imageOnlyRows([])).toEqual([]);
  });

  it('tolerates a missing rows array', () => {
    expect(imageOnlyRows(null)).toEqual([]);
    expect(imageOnlyRows(undefined)).toEqual([]);
  });
});
