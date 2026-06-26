// #161 Phase 3 (T8): the blacklist action must carry the opened item's canonical
// path (ref.path) so the backend routes the Bazarr blacklist call to the instance
// that owns that library. Id-only openers (no path) → instance 0 (back-compat).
// Pure payload-mapper test (node env, no rendering).
import { describe, it, expect } from 'vitest';
import { blacklistRequest } from '../blacklist-panel.jsx';

const epRow = {
  episode_id: 5,
  series_id: 9,
  provider: 'opensubtitles',
  subs_id: 'x',
  language: 'en',
  subtitles_path: '/m/x.en.srt',
};
const movieRow = {
  radarr_id: 3,
  provider: 'opensubtitles',
  subs_id: 'y',
  language: 'en',
  subtitles_path: '/m/y.en.srt',
};

describe('blacklistRequest (#161 P3 writeback routing)', () => {
  it('episode: includes canonical_path from ref.path and targets the episode endpoint', () => {
    const { url, body } = blacklistRequest(epRow, { path: '@anime/Show/ep.mkv', media_type: 'episode' });
    expect(url).toBe('/api/blacklist/episode');
    expect(body.canonical_path).toBe('@anime/Show/ep.mkv');
    expect(body.episode_id).toBe(5);
    expect(body.series_id).toBe(9);
    expect(body.subs_id).toBe('x');
  });

  it('movie: includes canonical_path from ref.path and targets the movie endpoint', () => {
    const { url, body } = blacklistRequest(movieRow, { path: '@films/Movie.mkv' });
    expect(url).toBe('/api/blacklist/movie');
    expect(body.canonical_path).toBe('@films/Movie.mkv');
    expect(body.radarr_id).toBe(3);
  });

  it('omits canonical_path when the opener supplied no path (media_type+id → instance 0)', () => {
    const { url, body } = blacklistRequest(epRow, { media_type: 'episode', id: 5 });
    expect(url).toBe('/api/blacklist/episode');
    expect('canonical_path' in body).toBe(false);
  });
});
