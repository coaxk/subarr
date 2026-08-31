import { describe, it, expect } from 'vitest';
import { runFailureMessage, detailFrom } from '../job-run-helpers.mjs';

describe('runFailureMessage', () => {
  it('returns null for success so the caller can treat null as "worked"', () => {
    expect(runFailureMessage(200, '')).toBe(null);
    expect(runFailureMessage(204, '')).toBe(null);
  });

  it('reports a 409 as could-not-run, preferring the server detail', () => {
    // The 409 path is the one that mattered: fetch does not reject on it, so
    // the old handler ran its success branch and the user saw nothing.
    expect(runFailureMessage(409, "job 'coverage-cache' could not be triggered right now"))
      .toBe("job 'coverage-cache' could not be triggered right now");
    expect(runFailureMessage(409, '')).toBe('could not run right now');
  });

  it('reports a 400 as unsupported, which means client and server disagree', () => {
    expect(runFailureMessage(400, '')).toBe('this job does not support run-now');
  });

  it('reports auth failures plainly rather than as a job failure', () => {
    expect(runFailureMessage(401, '')).toBe('not authorised');
    expect(runFailureMessage(403, '')).toBe('not authorised');
  });

  it('falls back to the status code for anything unexpected', () => {
    expect(runFailureMessage(500, '')).toBe('failed (HTTP 500)');
    expect(runFailureMessage(502, 'upstream boom')).toBe('upstream boom');
  });
});

describe('detailFrom', () => {
  it('extracts a string detail', () => {
    expect(detailFrom({ detail: 'nope' })).toBe('nope');
  });

  it('never throws on a body that is not the shape we expect', () => {
    // The response may not be JSON at all (a proxy error page, an empty body).
    expect(detailFrom(null)).toBe('');
    expect(detailFrom(undefined)).toBe('');
    expect(detailFrom('a string')).toBe('');
    expect(detailFrom({})).toBe('');
    // FastAPI returns a LIST of validation errors, not a string, for a 422.
    expect(detailFrom({ detail: [{ msg: 'bad' }] })).toBe('');
  });
});
