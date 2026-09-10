// #516: one reader for the `sonarr_propagation` block that POST
// /api/audio-lang/verifications returns inside a 200. Both the Review bulk
// path and the Coverage single-file modal use it, so "attempted and not ok"
// is decided in exactly one place.
import { describe, it, expect } from 'vitest';
import { propagationFailure, groupPropagationFailures } from '../audio-lang-propagation.mjs';

describe('propagationFailure', () => {
  it('returns the reason and detail when propagation was attempted and did not succeed', () => {
    const f = propagationFailure({
      verified: true,
      sonarr_propagation: { attempted: true, ok: false, reason: 'put_failed', detail: 'sonarr PUT failed: HTTP 500' },
    });
    expect(f).toEqual({ reason: 'put_failed', detail: 'sonarr PUT failed: HTTP 500' });
  });

  it('is null when propagation succeeded, was not attempted, or is absent', () => {
    expect(propagationFailure({ sonarr_propagation: { attempted: true, ok: true } })).toBeNull();
    expect(propagationFailure({ sonarr_propagation: { attempted: false } })).toBeNull();
    expect(propagationFailure({ verified: true })).toBeNull();
    expect(propagationFailure(null)).toBeNull();
    expect(propagationFailure(undefined)).toBeNull();
  });

  it('tolerates an old server that sends no reason', () => {
    const f = propagationFailure({ sonarr_propagation: { attempted: true, ok: false, detail: 'x' } });
    expect(f).toEqual({ reason: 'unknown', detail: 'x' });
  });
});

describe('groupPropagationFailures', () => {
  it('collapses identical reasons into one row with a count and a sample detail', () => {
    const rows = [
      { path: '/a', reason: 'episode_file_unresolved', detail: 'stale snapshot' },
      { path: '/b', reason: 'episode_file_unresolved', detail: 'stale snapshot' },
      { path: '/c', reason: 'put_failed', detail: 'HTTP 500' },
    ];
    expect(groupPropagationFailures(rows)).toEqual([
      { reason: 'episode_file_unresolved', count: 2, detail: 'stale snapshot' },
      { reason: 'put_failed', count: 1, detail: 'HTTP 500' },
    ]);
  });

  it('orders the most frequent reason first', () => {
    const rows = [
      { path: '/a', reason: 'put_failed', detail: 'x' },
      { path: '/b', reason: 'language_unsupported', detail: 'y' },
      { path: '/c', reason: 'language_unsupported', detail: 'y' },
    ];
    expect(groupPropagationFailures(rows).map((g) => g.reason)).toEqual(['language_unsupported', 'put_failed']);
  });

  it('returns an empty list for nothing', () => {
    expect(groupPropagationFailures([])).toEqual([]);
    expect(groupPropagationFailures(undefined)).toEqual([]);
  });
});
