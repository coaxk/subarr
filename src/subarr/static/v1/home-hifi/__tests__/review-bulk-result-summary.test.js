// #515: the banner's wording is derived from the runner's stats by one pure
// function. The component tests cover the file-failure and Stop lines through
// the DOM; this pins the arithmetic the DOM tests cannot reach - rule
// declarations that failed have no file path, so they are counted in `errors`
// but must never be listed as (or mistaken for) failed files.
import { describe, it, expect } from 'vitest';
import { bulkResultSummary } from '../review.jsx';

describe('bulkResultSummary (#515)', () => {
  it('separates file failures from remember-for-future rule failures', () => {
    const s = bulkResultSummary({
      done: 3, total: 3, errors: 2, failed: ['/a.mkv'], cancelled: false, remaining: [],
    });
    expect(s.failedFiles).toBe(1);
    expect(s.ruleFailures).toBe(1);
    expect(s.applied).toBe(2);
    expect(s.lines).toEqual([
      '2 of 3 failed.',
      '1 remember-for-future rule could not be saved.',
    ]);
  });

  it('a stop with no failures reports what was applied and what was never attempted', () => {
    const s = bulkResultSummary({
      done: 4, total: 6, errors: 0, failed: [], cancelled: true, remaining: ['/e.mkv', '/f.mkv'],
    });
    expect(s.lines).toEqual(['Stopped: 4 applied, 0 failed, 2 not attempted.']);
    expect(s.remaining).toBe(2);
  });

  it('a stop with failures says both', () => {
    const s = bulkResultSummary({
      done: 2, total: 5, errors: 1, failed: ['/b.mkv'], cancelled: true, remaining: ['/d.mkv', '/e.mkv', '/f.mkv'],
    });
    expect(s.lines).toEqual([
      'Stopped: 1 applied, 1 failed, 3 not attempted.',
      '1 of 5 failed.',
    ]);
  });

  it('tolerates a stats object missing the optional arrays', () => {
    const s = bulkResultSummary({ done: 1, total: 1, errors: 0 });
    expect(s.lines).toEqual([]);
    expect(s.failedFiles).toBe(0);
    expect(s.remaining).toBe(0);
  });
});
