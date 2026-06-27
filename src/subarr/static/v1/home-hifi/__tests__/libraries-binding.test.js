// #161 Phase 4B: LibrariesEditor binding helpers (node-env, no rendering).
import { describe, it, expect } from 'vitest';
import { instanceOptions, bindingLabel } from '../libraries-editor.jsx';

const INSTANCES = [
  { service: 'sonarr', id: '', name: 'default', is_default: true },
  { service: 'sonarr', id: 'anime', name: 'Anime', is_default: false },
  { service: 'bazarr', id: '', name: 'default', is_default: true },
];

describe('instanceOptions', () => {
  it('lists default first, then non-default instances of the service', () => {
    expect(instanceOptions(INSTANCES, 'sonarr')).toEqual([
      { id: '', label: 'default' },
      { id: 'anime', label: 'Anime' },
    ]);
  });

  it('returns just the default option for a service with no extras', () => {
    expect(instanceOptions(INSTANCES, 'radarr')).toEqual([{ id: '', label: 'default' }]);
  });

  it('handles null instances', () => {
    expect(instanceOptions(null, 'sonarr')).toEqual([{ id: '', label: 'default' }]);
  });
});

describe('bindingLabel', () => {
  it('shows the bound instance name', () => {
    expect(bindingLabel({ sonarr_id: 'anime' }, 'sonarr', INSTANCES)).toBe('Anime');
  });

  it('shows "default" when unbound (instance 0)', () => {
    expect(bindingLabel({ sonarr_id: '' }, 'sonarr', INSTANCES)).toBe('default');
    expect(bindingLabel({}, 'bazarr', INSTANCES)).toBe('default');
  });

  it('flags a binding to a missing instance', () => {
    expect(bindingLabel({ sonarr_id: 'gone' }, 'sonarr', INSTANCES)).toBe('gone (missing)');
  });
});
