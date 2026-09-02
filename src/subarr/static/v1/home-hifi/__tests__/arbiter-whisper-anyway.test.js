// #277: ArbiterModal "Whisper anyway" button must queue the job, not silently close.
// Tests the exported queueRow() helper that whisperAnyway calls — verifies it POSTs
// to /api/coverage/queue with the correct body for both episode-ID and path rows.
// Integration layer: the button wiring is confirmed by code inspection (onClick=whisperAnyway).
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { queueRow } from '../coverage.jsx';

beforeEach(() => {
  // Reset fetch stub before each test
  globalThis.fetch = undefined;
});

describe('queueRow', () => {
  it('POSTs to /api/coverage/queue with sonarr_episode_id when present', async () => {
    const spy = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({}),
    }));
    globalThis.fetch = spy;

    await queueRow({ _sonarr_episode_id: 42, _canonical_path: '/foo/bar.mkv' });

    expect(spy).toHaveBeenCalledTimes(1);
    const [url, opts] = spy.mock.calls[0];
    expect(url).toBe('/api/coverage/queue');
    expect(opts.method).toBe('POST');
    // [#485] CHANGED DELIBERATELY: the canonical now travels WITH the id.
    // Sending the id alone left the backend unable to tell which Sonarr
    // instance owned it, so it could resolve to another library's file.
    expect(JSON.parse(opts.body)).toEqual({
      sonarr_episode_id: 42,
      canonical_path: '/foo/bar.mkv',
    });
  });

  it('falls back to canonical_path when sonarr_episode_id is absent', async () => {
    const spy = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({}),
    }));
    globalThis.fetch = spy;

    await queueRow({ _canonical_path: '/mnt/nas/Media/Show/ep.mkv' });

    const [, opts] = spy.mock.calls[0];
    expect(JSON.parse(opts.body)).toEqual({ canonical_path: '/mnt/nas/Media/Show/ep.mkv' });
  });

  it('throws on non-ok, non-202 response', async () => {
    globalThis.fetch = vi.fn(async () => ({
      ok: false,
      status: 500,
      text: async () => 'Internal Server Error',
    }));

    await expect(queueRow({ _sonarr_episode_id: 99 })).rejects.toThrow('HTTP 500');
  });

  it('does not throw on 202 Accepted (async queue)', async () => {
    globalThis.fetch = vi.fn(async () => ({
      ok: false,
      status: 202,
      json: async () => ({ queued: true }),
    }));

    await expect(queueRow({ _sonarr_episode_id: 7 })).resolves.not.toThrow();
  });

  // #317 Slice B: "transcribe a full sub anyway" passes ignore_forced through.
  it('adds ignore_forced:true to the body when ignoreForced option is set', async () => {
    const spy = vi.fn(async () => ({ ok: true, status: 202, json: async () => ({}) }));
    globalThis.fetch = spy;

    await queueRow({ _sonarr_episode_id: 5 }, { ignoreForced: true });

    const [, opts] = spy.mock.calls[0];
    expect(JSON.parse(opts.body)).toEqual({ sonarr_episode_id: 5, ignore_forced: true });
  });

  it('omits ignore_forced by default (normal queue)', async () => {
    const spy = vi.fn(async () => ({ ok: true, status: 202, json: async () => ({}) }));
    globalThis.fetch = spy;

    await queueRow({ _canonical_path: '/m/x.mkv' });

    const [, opts] = spy.mock.calls[0];
    expect(JSON.parse(opts.body)).toEqual({ canonical_path: '/m/x.mkv' });
    expect('ignore_forced' in JSON.parse(opts.body)).toBe(false);
  });
});
