/**
 * #671: the Gaps "reason" chip for Bazarr's wanted list.
 *
 * deriveReason is not exported, so these tests go through normalizeRow and
 * assert the `reason` it emits for each shape the backend can send. A movie
 * row never carries `bazarr.episode_id` (that is an episode id), so the row
 * has to be recognised by the top-level `bazarr_wanted` flag instead.
 */
import { describe, it, expect } from 'vitest';
import { normalizeRow } from '../coverage.jsx';

const base = {
  media_type: 'movie',
  title: '21 Bridges',
  canonical_path: 'Movies/21 Bridges (2019)',
  verification_state: 'verified',
  monitored: true,
  embedded_en: null,
  bazarr_blind: false,
};

function reasonOf(item) {
  return normalizeRow(item, 0, 0).reason;
}

describe('gaps reason chip for Bazarr wanted rows (#671)', () => {
  it('a movie on Bazarr\'s wanted list reads bazarr-wanted', () => {
    const item = {
      ...base,
      bazarr_wanted: true,
      bazarr: { radarr_id: 7, episode_id: null, missing_subtitles: ['en'] },
    };
    expect(reasonOf(item)).toBe('bazarr-wanted');
  });

  it('a Radarr-only movie with bazarr_wanted false reads no-track', () => {
    const item = {
      ...base,
      bazarr_wanted: false,
      bazarr: { radarr_id: 7, episode_id: null, missing_subtitles: ['en'] },
    };
    expect(reasonOf(item)).toBe('no-track');
  });

  it('a movie from a snapshot without bazarr_wanted reads no-track', () => {
    const item = {
      ...base,
      bazarr: { radarr_id: 7, episode_id: null },
    };
    expect(reasonOf(item)).toBe('no-track');
  });

  it('an episode on Bazarr\'s wanted list reads bazarr-wanted', () => {
    const item = {
      ...base,
      media_type: 'episode',
      bazarr_wanted: true,
      bazarr: { sonarr_id: 3, episode_id: 99 },
    };
    expect(reasonOf(item)).toBe('bazarr-wanted');
  });

  it('an episode from a snapshot without bazarr_wanted still reads bazarr-wanted', () => {
    const item = {
      ...base,
      media_type: 'episode',
      bazarr: { episode_id: 99 },
    };
    expect(reasonOf(item)).toBe('bazarr-wanted');
  });

  it('bazarr_wanted as the string "true" does not read bazarr-wanted', () => {
    const item = {
      ...base,
      bazarr_wanted: 'true',
      bazarr: { radarr_id: 7 },
    };
    expect(reasonOf(item)).toBe('no-track');
  });

  it('an unmonitored movie reads unmonitored even when bazarr_wanted is true', () => {
    const item = { ...base, monitored: false, bazarr_wanted: true };
    expect(reasonOf(item)).toBe('unmonitored');
  });

  it('a bazarr-blind movie reads audio-mislabel even when bazarr_wanted is true', () => {
    const item = { ...base, bazarr_blind: true, bazarr_wanted: true };
    expect(reasonOf(item)).toBe('audio-mislabel');
  });

  it('a verified bazarr-blind movie reads awaiting-bazarr-sync even when bazarr_wanted is true', () => {
    const item = { ...base, bazarr_blind: true, audio_verified: true, bazarr_wanted: true };
    expect(reasonOf(item)).toBe('awaiting-bazarr-sync');
  });

  it('a movie with embedded EN reads embedded-only even when bazarr_wanted is true', () => {
    const item = { ...base, embedded_en: 'EN', bazarr_wanted: true };
    expect(reasonOf(item)).toBe('embedded-only');
  });

  it('a movie with embedded EN(SDH) reads embedded-only even when bazarr_wanted is true', () => {
    const item = { ...base, embedded_en: 'EN(SDH)', bazarr_wanted: true };
    expect(reasonOf(item)).toBe('embedded-only');
  });

  it('a wanted movie with no bazarr object still reads bazarr-wanted', () => {
    const item = { ...base, bazarr_wanted: true };
    expect(reasonOf(item)).toBe('bazarr-wanted');
  });
});
