// #368 follow-up: the /api/coverage/queue payload mapper (node-env, pure).
import { describe, it, expect } from 'vitest';
import { coverageQueueBody } from '../coverage.jsx';

describe('coverageQueueBody', () => {
  it('routes an episode row by sonarr_episode_id', () => {
    const body = coverageQueueBody({ _sonarr_episode_id: 1011, _canonical_path: 'TV/x.mkv' });
    expect(body).toEqual({ sonarr_episode_id: 1011 });
  });

  it('routes a movie row by canonical_path AND carries radarr_movie_id', () => {
    const body = coverageQueueBody({
      _sonarr_episode_id: null,
      _radarr_movie_id: 909,
      _canonical_path: 'Movies/Dune (2021)/Dune.mkv',
    });
    expect(body).toEqual({ canonical_path: 'Movies/Dune (2021)/Dune.mkv', radarr_movie_id: 909 });
  });

  it('omits radarr_movie_id for an episode row (null) — byte-identical to before', () => {
    const body = coverageQueueBody({ _sonarr_episode_id: 1011, _radarr_movie_id: null });
    expect(body).toEqual({ sonarr_episode_id: 1011 });
    expect('radarr_movie_id' in body).toBe(false);
  });

  it('adds ignore_forced when requested', () => {
    const body = coverageQueueBody({ _canonical_path: 'TV/x.mkv' }, { ignoreForced: true });
    expect(body).toEqual({ canonical_path: 'TV/x.mkv', ignore_forced: true });
  });
});
