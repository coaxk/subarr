// #161 Phase 4B: InstancesEditor pure grouping helper (node-env, no rendering).
import { describe, it, expect } from 'vitest';
import { groupByService } from '../instances-editor.jsx';

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
