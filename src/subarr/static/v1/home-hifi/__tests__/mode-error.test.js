// #524: the per-language tuning card rendered "HTTP 503" for every failure to
// read subgen's compose, while the server's `detail` already said what was
// wrong (a permission denial, a missing file, invalid YAML). Show the detail.
import { describe, it, expect } from 'vitest';
import { modeErrorMessage } from '../mode-error.mjs';

describe('modeErrorMessage', () => {
  it('prefers the server detail when the body carries one', () => {
    expect(modeErrorMessage(503, { detail: 'subarr (uid 1000) is not allowed to read /x' }))
      .toBe('subarr (uid 1000) is not allowed to read /x');
  });

  it('falls back to the status when there is no usable detail', () => {
    expect(modeErrorMessage(503, null)).toBe('HTTP 503');
    expect(modeErrorMessage(500, {})).toBe('HTTP 500');
    expect(modeErrorMessage(422, { detail: [{ loc: ['x'] }] })).toBe('HTTP 422');
  });
});
