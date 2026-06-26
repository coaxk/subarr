// #161 Phase 3 (T8): the arbiter "accept this provider sub" action must carry
// the row's canonical path so the backend routes the Bazarr download to the
// instance that owns the row's library. Absent canonical → instance 0 (back-compat).
// Pure payload-mapper test (node env, no rendering) — mirrors queueRow's harness.
import { describe, it, expect } from 'vitest';
import { arbiterAcceptBody } from '../coverage.jsx';

const cand = {
  provider: 'opensubtitles',
  subtitle: 'sub-123',
  score: 7,
  forced: 'False',
  hearing_impaired: 'False',
};

describe('arbiterAcceptBody (#161 P3 writeback routing)', () => {
  it('includes canonical_path from row._canonical_path so the accept routes to the owning Bazarr', () => {
    const body = arbiterAcceptBody(
      { _sonarr_episode_id: 42, _canonical_path: '@anime/Show/ep.mkv' },
      cand,
    );
    expect(body.canonical_path).toBe('@anime/Show/ep.mkv');
    expect(body.episode_id).toBe(42);
    expect(body.provider).toBe('opensubtitles');
    expect(body.subtitles_id).toBe('sub-123');
  });

  it('omits canonical_path when the row has none (back-compat → instance 0)', () => {
    const body = arbiterAcceptBody({ _sonarr_episode_id: 7 }, cand);
    expect('canonical_path' in body).toBe(false);
    expect(body.episode_id).toBe(7);
  });

  it('preserves forced/hi coercion from the candidate', () => {
    const body = arbiterAcceptBody(
      { _sonarr_episode_id: 1, _canonical_path: 'Show/x.mkv' },
      { provider: 'p', subs_id: 's', score: 3, forced: 'True', hearing_impaired: 'True' },
    );
    expect(body.forced).toBe(true);
    expect(body.hi).toBe(true);
    expect(body.subtitles_id).toBe('s');
  });
});
