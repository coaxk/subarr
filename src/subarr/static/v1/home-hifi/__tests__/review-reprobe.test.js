// #506: force re-probe. The reporter corrected `und` audio tags externally and
// Review kept showing the old value, because the probe cache is keyed on
// (path, mtime, size) and a tag edit can move neither.
//
// summariseReprobe is the user-facing outcome of a run. It is pure and tested
// here because of #515: the bulk-verify runner counted its failures and then
// destroyed the count when the batch ended, so a run where every single write
// failed rendered identically to a clean one. A re-probe that failed must say
// so, and "must say so" is only real if something asserts it.
import { describe, it, expect } from 'vitest';
import { summariseReprobe, MAX_REPROBE_PATHS } from '../review.jsx';

describe('summariseReprobe', () => {
  it('reports a clean run as ok', () => {
    const s = summariseReprobe({ status: 'done', total_files: 3, probed: 3, errors: [] }, 3);
    expect(s.ok).toBe(true);
    expect(s.text).toBe('Re-probed 3 of 3 file(s).');
    expect(s.errors).toEqual([]);
  });

  it('is NOT ok when any file failed, and carries the failures through', () => {
    const errors = [{ path: 'TV/S/e1.mkv', error: 'ffprobe exited 1' }];
    const s = summariseReprobe({ status: 'done', total_files: 2, probed: 1, errors }, 2);
    expect(s.ok).toBe(false);
    expect(s.text).toContain('1 failed');
    expect(s.errors).toEqual(errors);
  });

  it('a wholly failed run does not read like a clean one', () => {
    const clean = summariseReprobe({ status: 'done', total_files: 2, probed: 2, errors: [] }, 2);
    const broken = summariseReprobe({
      status: 'done',
      total_files: 2,
      probed: 0,
      errors: [{ path: 'a', error: 'x' }, { path: 'b', error: 'y' }],
    }, 2);
    expect(broken.ok).not.toBe(clean.ok);
    expect(broken.text).not.toBe(clean.text);
  });

  it('surfaces a walk that errored out', () => {
    const s = summariseReprobe({ status: 'error', total_files: 5, probed: 2, errors: [] }, 5);
    expect(s.ok).toBe(false);
    expect(s.text).toContain('failed');
  });

  it('surfaces a cancelled walk rather than calling it done', () => {
    const s = summariseReprobe({ status: 'cancelled', total_files: 5, probed: 2, errors: [] }, 5);
    expect(s.ok).toBe(false);
    expect(s.text).toContain('cancelled');
  });

  it('falls back to the requested count when the server reported no total', () => {
    const s = summariseReprobe({ status: 'done', errors: [] }, 7);
    expect(s.text).toBe('Re-probed 0 of 7 file(s).');
  });

  it('survives a missing or malformed state without throwing', () => {
    expect(() => summariseReprobe(null, 2)).not.toThrow();
    expect(() => summariseReprobe(undefined, 0)).not.toThrow();
    expect(summariseReprobe(null, 2).ok).toBe(true);
  });
});

describe('re-probe batch cap', () => {
  // Mirrors MAX_REPROBE_PATHS in src/subarr/routers/probe.py, which returns
  // 400 above it. tests/test_probe_router.py reads review.jsx and asserts the
  // two are equal, so this literal cannot drift on its own.
  it('matches the documented server cap', () => {
    expect(MAX_REPROBE_PATHS).toBe(500);
  });
});
