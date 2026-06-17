// #238 follow-up: session-expiry guard decision logic.
import { describe, it, expect } from 'vitest';
import { shouldGuardRedirect } from '../session-guard.js';

const ORIGIN = 'http://localhost:9923';

describe('shouldGuardRedirect', () => {
  it('redirects on a same-origin /api 401 from a normal page', () => {
    expect(shouldGuardRedirect(401, '/api/coverage', ORIGIN, '/coverage')).toBe(true);
  });

  it('ignores non-401 statuses', () => {
    expect(shouldGuardRedirect(200, '/api/coverage', ORIGIN, '/coverage')).toBe(false);
    expect(shouldGuardRedirect(500, '/api/coverage', ORIGIN, '/coverage')).toBe(false);
  });

  it('ignores /api/auth/* (expected bad-login 401s)', () => {
    expect(shouldGuardRedirect(401, '/api/auth/login', ORIGIN, '/login')).toBe(false);
    expect(shouldGuardRedirect(401, '/api/auth/state', ORIGIN, '/home')).toBe(false);
  });

  it('does not loop while already on the login/setup page', () => {
    expect(shouldGuardRedirect(401, '/api/coverage', ORIGIN, '/login')).toBe(false);
    expect(shouldGuardRedirect(401, '/api/coverage', ORIGIN, '/setup')).toBe(false);
  });

  it('ignores non-/api paths and cross-origin URLs', () => {
    expect(shouldGuardRedirect(401, '/static/x.js', ORIGIN, '/home')).toBe(false);
    expect(shouldGuardRedirect(401, 'https://evil.com/api/x', ORIGIN, '/home')).toBe(false);
  });

  it('handles absolute same-origin URLs', () => {
    expect(shouldGuardRedirect(401, ORIGIN + '/api/queue', ORIGIN, '/queue')).toBe(true);
  });
});
