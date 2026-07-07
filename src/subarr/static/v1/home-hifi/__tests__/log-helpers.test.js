// #157 gap-fill — pure helpers for the Logs source switcher + Health recent
// errors row. Node-env, no DOM.
import { describe, it, expect } from 'vitest';
import { nextLogSource, formatRecentRow, LOG_SOURCES } from '../log-helpers.mjs';

describe('nextLogSource', () => {
  it('toggles subgen -> subarr and back', () => {
    expect(nextLogSource('subgen')).toBe('subarr');
    expect(nextLogSource('subarr')).toBe('subgen');
  });

  it('falls back to subgen for an unknown source', () => {
    expect(nextLogSource('bogus')).toBe('subgen');
    expect(nextLogSource(null)).toBe('subgen');
  });

  it('exposes the two supported sources', () => {
    expect(LOG_SOURCES).toEqual(['subgen', 'subarr']);
  });
});

describe('formatRecentRow', () => {
  it('maps a ring record to a display row', () => {
    const row = formatRecentRow({
      ts: 1751846400,
      level: 'WARNING',
      logger_name: 'subarr.coverage_engine',
      message: 'coverage build slow',
      exc_text: null,
    });
    expect(row.level).toBe('WARNING');
    expect(row.logger).toBe('subarr.coverage_engine');
    expect(row.message).toBe('coverage build slow');
    expect(row.hasTrace).toBe(false);
    expect(row.exc_text).toBe(null);
  });

  it('flags a record that carries a traceback', () => {
    const row = formatRecentRow({
      ts: 1751846400,
      level: 'ERROR',
      logger_name: 'subarr.app',
      message: 'boom',
      exc_text: 'Traceback (most recent call last): ...',
    });
    expect(row.hasTrace).toBe(true);
    expect(row.exc_text).toContain('Traceback');
  });

  it('tolerates a missing/partial record', () => {
    const row = formatRecentRow({});
    expect(row.level).toBe('INFO');
    expect(row.logger).toBe('');
    expect(row.message).toBe('');
    expect(row.hasTrace).toBe(false);
  });
});
