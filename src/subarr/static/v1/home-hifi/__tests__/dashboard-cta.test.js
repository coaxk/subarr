// #202 activation: first-walk empty-state decision helper.
import { describe, it, expect } from 'vitest';
import { shouldShowFirstWalkCta } from '../dashboard.jsx';

describe('shouldShowFirstWalkCta', () => {
  it('is false before data has loaded (no flash)', () => {
    expect(shouldShowFirstWalkCta(null)).toBe(false);
    expect(shouldShowFirstWalkCta(undefined)).toBe(false);
  });

  it('is true when loaded with zero stage counts and no activity', () => {
    expect(shouldShowFirstWalkCta({ stages: [{ count: 0 }, { count: 0 }], activity: [] })).toBe(true);
    expect(shouldShowFirstWalkCta({})).toBe(true); // loaded but totally empty
  });

  it('is false when any stage has a non-zero count', () => {
    expect(shouldShowFirstWalkCta({ stages: [{ count: 0 }, { count: 7 }], activity: [] })).toBe(false);
  });

  it('is false when the activity feed has entries', () => {
    expect(shouldShowFirstWalkCta({ stages: [{ count: 0 }], activity: [{ kind: 'probed' }] })).toBe(false);
  });
});
