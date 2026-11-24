/**
 * #671: a plain `<stem>.srt` sidecar with no language in its filename.
 *
 * deriveReason is not exported, so these tests go through normalizeRow and
 * assert the `reason` it emits. The backend ships `sidecar_no_lang` as an
 * array of filenames; older cached snapshots lack the key entirely.
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
  bazarr_wanted: false,
  bazarr: { radarr_id: 7, episode_id: null, missing_subtitles: ['en'] },
};

function reasonOf(item) {
  return normalizeRow(item, 0, 0).reason;
}

describe('gaps reason chip for a plain srt sidecar (#671)', () => {
  it('a movie with a plain srt sidecar reads sidecar-no-lang', () => {
    const item = { ...base, sidecar_no_lang: ['21 Bridges 1080p (2019).srt'] };
    expect(reasonOf(item)).toBe('sidecar-no-lang');
  });

  it('an empty sidecar_no_lang array reads no-track', () => {
    const item = { ...base, sidecar_no_lang: [] };
    expect(reasonOf(item)).toBe('no-track');
  });

  it('a snapshot without the sidecar_no_lang key reads no-track', () => {
    expect(reasonOf({ ...base })).toBe('no-track');
  });

  it('sidecar_no_lang as a string rather than an array reads no-track', () => {
    const item = { ...base, sidecar_no_lang: '21 Bridges.srt' };
    expect(reasonOf(item)).toBe('no-track');
  });

  it('sidecar_no_lang null reads no-track', () => {
    const item = { ...base, sidecar_no_lang: null };
    expect(reasonOf(item)).toBe('no-track');
  });

  it('a wanted movie with a plain srt sidecar reads bazarr-wanted', () => {
    const item = { ...base, bazarr_wanted: true, sidecar_no_lang: ['x.srt'] };
    expect(reasonOf(item)).toBe('bazarr-wanted');
  });

  it('a snapshot row with only an episode_id and a plain srt sidecar reads bazarr-wanted', () => {
    const { bazarr_wanted, ...rest } = base;
    const item = { ...rest, bazarr: { episode_id: 99 }, sidecar_no_lang: ['x.srt'] };
    expect(reasonOf(item)).toBe('bazarr-wanted');
  });

  it('an unmonitored movie with a plain srt sidecar reads unmonitored', () => {
    const item = { ...base, monitored: false, sidecar_no_lang: ['x.srt'] };
    expect(reasonOf(item)).toBe('unmonitored');
  });

  it('a movie with embedded EN and a plain srt sidecar reads embedded-only', () => {
    const item = { ...base, embedded_en: 'EN', sidecar_no_lang: ['x.srt'] };
    expect(reasonOf(item)).toBe('embedded-only');
  });

  it('a bazarr-blind movie with a plain srt sidecar reads audio-mislabel', () => {
    const item = { ...base, bazarr_blind: true, sidecar_no_lang: ['x.srt'] };
    expect(reasonOf(item)).toBe('audio-mislabel');
  });

  it('a verified bazarr-blind movie with a plain srt sidecar reads awaiting-bazarr-sync', () => {
    const item = { ...base, bazarr_blind: true, audio_verified: true, sidecar_no_lang: ['x.srt'] };
    expect(reasonOf(item)).toBe('awaiting-bazarr-sync');
  });

  it('an unprobed movie with a plain srt sidecar reads sidecar-no-lang', () => {
    const item = { ...base, verification_state: 'unprobed', sidecar_no_lang: ['x.srt'] };
    expect(reasonOf(item)).toBe('sidecar-no-lang');
  });
});
