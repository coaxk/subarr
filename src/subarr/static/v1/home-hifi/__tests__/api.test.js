// #238: apiFetch global 401 interceptor.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { apiFetch } from '../api.jsx';

beforeEach(() => {
  globalThis.window = {
    location: { pathname: '/dashboard', search: '?tab=x', assign: vi.fn() },
  };
});

describe('apiFetch', () => {
  it('returns the response on 200 and does not redirect', async () => {
    globalThis.fetch = vi.fn(async () => ({ status: 200, ok: true }));
    const r = await apiFetch('/api/thing');
    expect(r.status).toBe(200);
    expect(window.location.assign).not.toHaveBeenCalled();
  });

  it('throws on 401 (the global session guard owns the redirect+notice)', async () => {
    globalThis.fetch = vi.fn(async () => ({ status: 401 }));
    await expect(apiFetch('/api/thing')).rejects.toThrow('unauthenticated');
    // apiFetch must NOT redirect itself — that would preempt the guard's notice.
    expect(window.location.assign).not.toHaveBeenCalled();
  });

  it('passes non-401 responses through without redirecting', async () => {
    globalThis.fetch = vi.fn(async () => ({ status: 500, ok: false }));
    const r = await apiFetch('/api/thing');
    expect(r.status).toBe(500);
    expect(window.location.assign).not.toHaveBeenCalled();
  });

  it('sends same-origin credentials and merges opts', async () => {
    const spy = vi.fn(async () => ({ status: 200 }));
    globalThis.fetch = spy;
    await apiFetch('/api/x', { method: 'POST' });
    expect(spy).toHaveBeenCalledWith('/api/x', { credentials: 'same-origin', method: 'POST' });
  });
});
