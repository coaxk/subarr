// Task 8: ArenaSlotIndicator renders nothing when inactive, shows sweep text
// when active. The harness runs in Node with stubbed React (no real rendering)
// — we test the exported arenaSlotText() helper that drives the component copy.
import { describe, it, expect } from 'vitest';
import { arenaSlotText } from '../queue.jsx';

describe('arenaSlotText', () => {
  it('returns null when arenaActive is 0 (renders nothing)', () => {
    expect(arenaSlotText(0)).toBeNull();
  });

  it('returns null when arenaActive is falsy', () => {
    expect(arenaSlotText(null)).toBeNull();
    expect(arenaSlotText(undefined)).toBeNull();
  });

  it('returns text matching /tuning lab sweep running/i when active', () => {
    const t = arenaSlotText(1);
    expect(t).toMatch(/tuning lab sweep running/i);
  });

  it('says GPU slot (singular) for 1', () => {
    expect(arenaSlotText(1)).toMatch(/1 GPU slot\b(?!s)/);
  });

  it('says GPU slots (plural) for 2', () => {
    expect(arenaSlotText(2)).toMatch(/2 GPU slots/);
  });
});
