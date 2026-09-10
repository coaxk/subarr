// #479: the dashboard banner for a subgen that has been unreachable for a
// while. Pure decisions pinned here; the component is covered in jsdom.
import { describe, it, expect } from 'vitest';
import { outageMessage, shouldShowOutage, formatDuration } from '../subgen-outage.mjs';

describe('formatDuration', () => {
  it('reads in the largest two units that matter', () => {
    expect(formatDuration(45)).toBe('45 seconds');
    expect(formatDuration(600)).toBe('10 minutes');
    expect(formatDuration(3600 * 3 + 60 * 4)).toBe('3 hours 4 minutes');
    expect(formatDuration(86400 * 66 + 3600 * 5)).toBe('66 days 5 hours');
  });
});

describe('shouldShowOutage', () => {
  const outage = { since: 1000, seconds: 900, cause: 'refused', consecutive: 30 };

  it('shows after ten minutes, not on the first missed probe', () => {
    expect(shouldShowOutage({ ...outage, seconds: 90 }, null)).toBe(false);
    expect(shouldShowOutage({ ...outage, seconds: 600 }, null)).toBe(true);
  });

  it('stays hidden once dismissed for THIS outage, and returns for the next one', () => {
    expect(shouldShowOutage(outage, 1000)).toBe(false);
    expect(shouldShowOutage({ ...outage, since: 5000 }, 1000)).toBe(true);
  });

  it('is never shown for no outage', () => {
    expect(shouldShowOutage(null, null)).toBe(false);
    expect(shouldShowOutage(undefined, null)).toBe(false);
  });
});

describe('outageMessage', () => {
  it('names the duration and cause, and says what stops working', () => {
    const m = outageMessage({ since: 0, seconds: 86400 * 3, cause: 'refused', hint: 'Check the port.' });
    expect(m.title).toBe('subgen has been unreachable for 3 days');
    expect(m.body).toContain('Nothing is being transcribed');
    expect(m.body).toContain('refused');
    expect(m.hint).toBe('Check the port.');
  });
});
