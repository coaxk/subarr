// #161 Phase 4B: InstancesEditor pure grouping helper (node-env, no rendering).
import { describe, it, expect } from 'vitest';
import { groupByService, indexHealth, dotKindForInstance } from '../instances-editor.jsx';

describe('groupByService', () => {
  it('groups instances per service with the default instance first', () => {
    const out = groupByService([
      { service: 'sonarr', id: 'anime', name: 'Anime', is_default: false },
      { service: 'sonarr', id: '', name: 'default', is_default: true },
      { service: 'bazarr', id: '', name: 'default', is_default: true },
    ]);
    expect(out.sonarr.map((i) => i.id)).toEqual(['', 'anime']);
    expect(out.bazarr.map((i) => i.id)).toEqual(['']);
    expect(out.radarr).toEqual([]);
  });

  it('sorts non-default instances by name after the default', () => {
    const out = groupByService([
      { service: 'sonarr', id: 'zeta', name: 'Zeta', is_default: false },
      { service: 'sonarr', id: 'alpha', name: 'Alpha', is_default: false },
      { service: 'sonarr', id: '', name: 'default', is_default: true },
    ]);
    expect(out.sonarr.map((i) => i.id)).toEqual(['', 'alpha', 'zeta']);
  });

  it('returns empty per-service buckets for null/empty input', () => {
    expect(groupByService(null)).toEqual({ sonarr: [], radarr: [], bazarr: [] });
    expect(groupByService([])).toEqual({ sonarr: [], radarr: [], bazarr: [] });
  });
});

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
  const inst = { service: 'sonarr', id: 'anime', has_api_key: true, url: 'http://x' };

  it('is ok when the live probe says online', () => {
    expect(dotKindForInstance(inst, { configured: true, online: true })).toBe('ok');
  });

  it('is error when configured but the live probe says offline', () => {
    expect(dotKindForInstance(inst, { configured: true, online: false })).toBe('error');
  });

  it('is muted when the instance is not configured', () => {
    expect(dotKindForInstance(inst, { configured: false, online: false })).toBe('muted');
  });

  it('is muted (unknown) while health is still pending', () => {
    expect(dotKindForInstance(inst, undefined)).toBe('muted');
  });
});
