// #368 follow-up: the /api/coverage/queue payload mapper (node-env, pure).
import { describe, it, expect } from 'vitest';
import { coverageQueueBody } from '../coverage.jsx';

describe('coverageQueueBody', () => {
  it('routes an episode row by sonarr_episode_id', () => {
    // [#485] CHANGED DELIBERATELY. This used to assert the canonical was
    // OMITTED when an episode id was present, which is exactly the bug:
    // the id alone cannot tell the backend which Sonarr instance owns it,
    // so it resolved against instance 0 and could target another library's
    // file. The canonical must travel WITH the id.
    const body = coverageQueueBody({ _sonarr_episode_id: 1011, _canonical_path: 'TV/x.mkv' });
    expect(body).toEqual({ sonarr_episode_id: 1011, canonical_path: 'TV/x.mkv' });
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

// [#485] The payload must carry the canonical path even when the row has a
// Sonarr episode id.
//
// Episode ids are unique only WITHIN a Sonarr instance. Sending the id alone
// left the backend no way to know which instance owned it, so it resolved
// against instance 0 and, on a multi-instance install, targeted a different
// library's file. Reported by AztecGuyGDL: clicking a row in the anime library
// queued an episode of an unrelated show in the tv library.
//
// The canonical is what identifies the library, so it has to travel with the id.
describe('coverageQueueBody carries the canonical for instance scoping (#485)', () => {
  it('sends BOTH when the row has an episode id and a path', () => {
    const body = coverageQueueBody({
      _sonarr_episode_id: 1011,
      _canonical_path: '@anime/Naruto/Season 1/Naruto.S01E01.mkv',
    });
    expect(body.sonarr_episode_id).toBe(1011);
    expect(body.canonical_path).toBe('@anime/Naruto/Season 1/Naruto.S01E01.mkv');
  });

  it('still sends the id alone when the row genuinely has no path', () => {
    const body = coverageQueueBody({ _sonarr_episode_id: 1011 });
    expect(body.sonarr_episode_id).toBe(1011);
    expect('canonical_path' in body).toBe(false);
  });

  it('still sends the path alone for a row with no episode id', () => {
    const body = coverageQueueBody({ _canonical_path: 'TV/x.mkv' });
    expect(body.canonical_path).toBe('TV/x.mkv');
    expect('sonarr_episode_id' in body).toBe(false);
  });

  it('keeps the library slug intact, since that is what selects the instance', () => {
    // A stripped '@anime/' head would silently resolve to library 0 again.
    const body = coverageQueueBody({
      _sonarr_episode_id: 7, _canonical_path: '@anime/x/y.mkv',
    });
    expect(body.canonical_path.startsWith('@anime/')).toBe(true);
  });
});
