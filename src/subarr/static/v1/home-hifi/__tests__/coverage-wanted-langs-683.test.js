/**
 * #683: the WANTED column should read the row's top-level `wanted_langs`
 * list when the backend sends it, and fall back to
 * `bazarr.missing_subtitles` for older cached snapshots.
 */
import { describe, it, expect } from 'vitest';
import { normalizeRow } from '../coverage.jsx';

const base = {
  media_type: 'movie',
  title: '21 Bridges',
  canonical_path: 'Movies/21 Bridges (2019)',
  verification_state: 'verified',
  monitored: true,
};

function langsOf(item) {
  return normalizeRow(item, 0).langs;
}

describe('gaps WANTED column reads wanted_langs (#683)', () => {
  it('wanted_langs is used even when bazarr.missing_subtitles is empty', () => {
    const item = {
      ...base,
      wanted_langs: ['en'],
      bazarr: { radarr_id: 11, missing_subtitles: [] },
    };
    expect(langsOf(item)).toEqual(['en']);
  });

  it('a snapshot without wanted_langs falls back to bazarr.missing_subtitles', () => {
    const item = {
      ...base,
      bazarr: { radarr_id: 11, missing_subtitles: ['nl'] },
    };
    expect(langsOf(item)).toEqual(['nl']);
  });

  it('an empty wanted_langs wins over a non-empty bazarr.missing_subtitles', () => {
    const item = {
      ...base,
      wanted_langs: [],
      bazarr: { missing_subtitles: ['en'] },
    };
    expect(langsOf(item)).toEqual([]);
  });

  it('a string wanted_langs falls back to bazarr.missing_subtitles', () => {
    const item = {
      ...base,
      wanted_langs: 'en',
      bazarr: { missing_subtitles: ['nl'] },
    };
    expect(langsOf(item)).toEqual(['nl']);
  });

  it('a null wanted_langs falls back to bazarr.missing_subtitles', () => {
    const item = {
      ...base,
      wanted_langs: null,
      bazarr: { missing_subtitles: ['nl'] },
    };
    expect(langsOf(item)).toEqual(['nl']);
  });

  it('no wanted_langs and no bazarr key reads no languages', () => {
    const item = { ...base };
    expect(langsOf(item)).toEqual([]);
  });

  it('no wanted_langs and a null bazarr reads no languages', () => {
    const item = { ...base, bazarr: null };
    expect(langsOf(item)).toEqual([]);
  });

  it('wanted_langs is de-duplicated and falsy entries are dropped', () => {
    const item = {
      ...base,
      wanted_langs: ['en', 'en', null, '', 'nl'],
      bazarr: { missing_subtitles: ['fr'] },
    };
    expect(langsOf(item)).toEqual(['en', 'nl']);
  });
});
