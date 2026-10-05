/**
 * #594 / #458 UI: image-only rows must actually reach the banner.
 *
 * The #458 ImageOnlyBanner could never render. `imageOnlyRows` filters on
 * `r.embedded_en`, but its only caller passes rows from `normalizeRow`, which
 * never emitted that key — so the filter always returned [] and the banner
 * always bailed on `!targets.length`. Its existing test passed because it
 * built row objects BY HAND carrying `embedded_en`: the function was correct
 * and the shape it was tested against was one nothing produces.
 *
 * So these tests go through normalizeRow deliberately. A test that constructs
 * its own row shape cannot catch this class of bug.
 */
import { describe, it, expect } from 'vitest';
import { imageOnlyRows, normalizeRow } from '../coverage.jsx';

const base = {
  media_type: 'movie',
  title: 'Whiplash',
  canonical_path: 'Movies/Whiplash',
  verification_state: 'verified',
  monitored: true,
};

describe('image-only rows through the real pipeline', () => {
  it('an embedded EN(image) row survives normalizeRow and reaches the banner', () => {
    const row = normalizeRow({ ...base, embedded_en: 'EN(image)' }, 0, 0);
    expect(imageOnlyRows([row]).map(r => r.title)).toEqual(['Whiplash']);
  });

  it('an external .idx/.sub pair also reaches the banner', () => {
    // #594: the pair is image-only English coverage just as much as an
    // embedded bitmap track is, so the same bulk bypass-skip offer applies.
    const row = normalizeRow({ ...base, image_subs_on_disk: ['Whiplash.idx'] }, 0, 0);
    expect(imageOnlyRows([row]).map(r => r.title)).toEqual(['Whiplash']);
  });

  it('a row with real text coverage is not offered', () => {
    const row = normalizeRow({ ...base, embedded_en: 'EN' }, 0, 0);
    expect(imageOnlyRows([row])).toEqual([]);
  });

  it('a plain gap with no subtitles at all is not offered', () => {
    const row = normalizeRow({ ...base, embedded_en: null }, 0, 0);
    expect(imageOnlyRows([row])).toEqual([]);
  });

  it('normalizeRow carries the fields the UI needs', () => {
    const row = normalizeRow(
      { ...base, embedded_en: 'EN(image)', image_subs_on_disk: ['W.idx'], image_only_subgen_will_skip: true },
      0, 0,
    );
    expect(row.embedded_en).toBe('EN(image)');
    expect(row.image_subs).toEqual(['W.idx']);
    expect(row.image_skip).toBe(true);
  });
});
