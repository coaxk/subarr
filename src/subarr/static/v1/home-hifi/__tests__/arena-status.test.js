// Task 7: StatusPill map includes waiting_for_capacity entry.
// The harness runs in Node with a stubbed React (no rendering) — we test
// the exported STATUS_MAP lookup, not a rendered component.
import { describe, it, expect } from 'vitest';
import { STATUS_MAP } from '../arena.jsx';

describe('STATUS_MAP', () => {
  it('includes waiting_for_capacity with text matching /waiting for subgen/i', () => {
    const entry = STATUS_MAP['waiting_for_capacity'];
    expect(entry).toBeTruthy();
    expect(entry.text).toMatch(/waiting for subgen/i);
  });

  it('maps known statuses correctly', () => {
    expect(STATUS_MAP['pending'].kind).toBe('idle');
    expect(STATUS_MAP['running'].kind).toBe('busy');
    expect(STATUS_MAP['done'].kind).toBe('ok');
    expect(STATUS_MAP['error'].kind).toBe('err');
  });

  it('waiting_for_capacity uses idle kind (not busy or err)', () => {
    expect(STATUS_MAP['waiting_for_capacity'].kind).toBe('idle');
  });
});
