// #378: shared per-instance health helpers (node-env, pure functions).
import { describe, it, expect } from 'vitest';
import { indexHealth, dotKindForInstance, instanceSubRows, computeHealthCount } from '../instance-health-util.mjs';

describe('indexHealth', () => {
  it('keys health records by service/id', () => {
    const m = indexHealth([
      { service: 'sonarr', id: '', online: true, configured: true },
      { service: 'sonarr', id: 'anime', online: false, configured: true },
    ]);
    expect(m['sonarr/'].online).toBe(true);
    expect(m['sonarr/anime'].online).toBe(false);
  });

  it('returns an empty map for null/empty input', () => {
    expect(indexHealth(null)).toEqual({});
    expect(indexHealth([])).toEqual({});
  });
});

describe('dotKindForInstance', () => {
  it('is ok when online, error when configured+down, muted otherwise', () => {
    expect(dotKindForInstance(null, { configured: true, online: true })).toBe('ok');
    expect(dotKindForInstance(null, { configured: true, online: false })).toBe('error');
    expect(dotKindForInstance(null, { configured: false, online: false })).toBe('muted');
    expect(dotKindForInstance(null, undefined)).toBe('muted');
  });
});

describe('instanceSubRows', () => {
  const health = [
    { service: 'sonarr', id: '', name: 'default', configured: true, online: true, detail: 'connected' },
    { service: 'sonarr', id: 'anime', name: 'Anime', configured: true, online: false, detail: 'connect timed out' },
    { service: 'bazarr', id: '', name: 'default', configured: true, online: true, detail: 'connected' },
  ];

  it('returns [] for a non-instanced service', () => {
    expect(instanceSubRows('plex', health)).toEqual([]);
    expect(instanceSubRows('tautulli', health)).toEqual([]);
  });

  it('returns [] when a service has only the default instance (single-stack stays identical)', () => {
    expect(instanceSubRows('bazarr', health)).toEqual([]);
  });

  it('returns one row per instance, default first, for a multi-instance service', () => {
    const rows = instanceSubRows('sonarr', health);
    expect(rows.map((r) => r.id)).toEqual(['', 'anime']);
    expect(rows[0]).toMatchObject({ name: 'default', kind: 'ok', label: 'connected' });
    expect(rows[1]).toMatchObject({ name: 'Anime', kind: 'error' });
    expect(rows[1].label).toContain('offline');
    expect(rows[1].label).toContain('connect timed out');
  });

  it('is case-insensitive on the service id and tolerates empty health', () => {
    expect(instanceSubRows('SONARR', health).map((r) => r.id)).toEqual(['', 'anime']);
    expect(instanceSubRows('sonarr', null)).toEqual([]);
  });
});

describe('computeHealthCount', () => {
  const intHealth = {
    integrations: [
      { name: 'sonarr', online: true },
      { name: 'bazarr', online: true },
    ],
    subgen: { reachable: true },
  };

  it('returns null when integrations health is missing', () => {
    expect(computeHealthCount(null, [])).toBe(null);
  });

  it('counts default integrations + subgen unchanged when there are no extra instances', () => {
    expect(computeHealthCount(intHealth, [])).toBe('3/3');
    expect(computeHealthCount(intHealth, null)).toBe('3/3');
  });

  it('does NOT double-count default instances (id "") from /api/instances/health', () => {
    const inst = [
      { service: 'sonarr', id: '', online: true, configured: true },
      { service: 'bazarr', id: '', online: true, configured: true },
    ];
    expect(computeHealthCount(intHealth, inst)).toBe('3/3');
  });

  it('folds a DOWN non-default instance into the denominator only', () => {
    const inst = [{ service: 'sonarr', id: 'anime', online: false, configured: true }];
    expect(computeHealthCount(intHealth, inst)).toBe('3/4');
  });

  it('folds an ONLINE non-default instance into both numerator and denominator', () => {
    const inst = [{ service: 'sonarr', id: 'anime', online: true, configured: true }];
    expect(computeHealthCount(intHealth, inst)).toBe('4/4');
  });
});
