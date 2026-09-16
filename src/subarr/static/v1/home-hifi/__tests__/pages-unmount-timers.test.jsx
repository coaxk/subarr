// @vitest-environment jsdom
//
// Same rule as review-unmount-timers.test.jsx, for the other two pages that
// leaked: nothing a page starts may outlive it.
//
// Coverage armed a silent refetch 2.5 s after every `audio-lang-verified`
// event, a refetch 10 s after a Bazarr sync, and polled `/api/coverage/status`
// every 2 s up to 90 times after "Probe now" — all detached from React, so
// leaving the page kept them running. Logs kept a 200 ms flush timer in a ref
// that unmount never cleared.
import { describe, it, expect, beforeAll, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

let CoveragePage, LogsPage;
let liveTimers, realSetTimeout, realClearTimeout, fetchCount, streams;

function loadVendoredReact() {
  for (const name of ['react.production.min.js', 'react-dom.production.min.js']) {
    const url = path.resolve(process.cwd(), 'src/subarr/static/v1/vendor', name);
    (0, eval)(fs.readFileSync(url, 'utf8'));
  }
}

beforeAll(async () => {
  loadVendoredReact();
  ({ CoveragePage } = await import('../coverage.jsx'));
  ({ LogsPage } = await import('../logs.jsx'));
});

const coveragePayload = {
  generated_at: 'g1',
  counts_by_reason: {},
  items: [{
    file_canonical_path: 'TV/Show/S01E01.mkv',
    canonical_path: '/TV/Show/S01E01.mkv',
    title: 'Show', episode_number: '1', media_type: 'show', library: 'TV',
    verification_state: 'unprobed', audio_langs: [], missing: [], wanted: [],
  }],
};

const reply = (status, body = {}) => ({
  ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body),
});

// A minimal EventSource: the Logs page needs one to exist, and the test needs
// to push a line through it to arm the flush timer.
class FakeEventSource {
  constructor(url) {
    this.url = url;
    this.listeners = {};
    streams.push(this);
  }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  emit(name, data) { for (const fn of this.listeners[name] || []) fn({ data }); }
  close() { this.closed = true; }
}

beforeEach(() => {
  fetchCount = 0;
  streams = [];
  liveTimers = new Set();
  realSetTimeout = globalThis.setTimeout;
  realClearTimeout = globalThis.clearTimeout;
  const track = (fn, ms, ...rest) => {
    const id = realSetTimeout((...a) => { liveTimers.delete(id); return fn(...a); }, ms, ...rest);
    liveTimers.add(id);
    return id;
  };
  const untrack = (id) => { liveTimers.delete(id); return realClearTimeout(id); };
  globalThis.setTimeout = track; window.setTimeout = track;
  globalThis.clearTimeout = untrack; window.clearTimeout = untrack;
  globalThis.EventSource = FakeEventSource; window.EventSource = FakeEventSource;

  const stub = async (url) => {
    fetchCount += 1;
    const u = String(url);
    if (u.startsWith('/api/coverage/status')) return reply(200, { refreshing: true });
    if (u.startsWith('/api/coverage')) return reply(200, coveragePayload);
    return reply(200, {});
  };
  globalThis.fetch = stub; window.fetch = stub;
  window.alert = () => {};
  window.confirm = () => true;
});

const roots = [];
afterEach(() => {
  for (const r of roots.splice(0)) r.unmount();
  document.body.innerHTML = '';
  globalThis.setTimeout = realSetTimeout; window.setTimeout = realSetTimeout;
  globalThis.clearTimeout = realClearTimeout; window.clearTimeout = realClearTimeout;
});

const sleepReal = (ms) => new Promise((r) => realSetTimeout(r, ms));

async function until(pred, { timeout = 4000, every = 10, what = 'condition' } = {}) {
  const t0 = Date.now();
  for (;;) {
    const v = pred();
    if (v) return v;
    if (Date.now() - t0 > timeout) throw new Error(`timed out waiting for ${what}`);
    await sleepReal(every);
  }
}

async function render(Component) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container);
  roots.push(root);
  root.render(React.createElement(Component));
  await until(() => container.textContent && container.textContent.length > 0,
    { what: 'the page to render' });
  return { container, root };
}

describe('Coverage stops its own timers when it unmounts', () => {
  it('does not refetch after a verification event once the page is gone', async () => {
    const { root } = await render(CoveragePage);
    await until(() => fetchCount > 0, { what: 'the first coverage fetch' });

    // Every verified file arms a silent refetch 2.5 s out.
    window.dispatchEvent(new CustomEvent('audio-lang-verified', {
      detail: { file_canonical_path: 'TV/Show/S01E01.mkv', lang_code: 'en' },
    }));
    await sleepReal(50);

    root.unmount();
    roots.splice(0);
    const afterUnmount = fetchCount;
    await sleepReal(3000);          // the 2.5 s refetch would have landed
    expect(fetchCount, 'Coverage refetched after unmount').toBe(afterUnmount);
    expect(Array.from(liveTimers), 'Coverage left a timer armed').toEqual([]);
  });

  // Deliberately waits out two 2 s poll rounds, so it needs more than the 5 s default.
  it('stops the "Probe now" poll instead of polling a page that is gone', { timeout: 20000 }, async () => {
    const { container, root } = await render(CoveragePage);
    await until(() => fetchCount > 0, { what: 'the first coverage fetch' });
    const probe = await until(
      () => Array.from(container.querySelectorAll('button')).find((b) => /Probe now/.test(b.textContent || '')),
      { what: 'the Probe now button' });
    probe.click();
    // /api/coverage/status answers `refreshing: true` forever, so the poll
    // keeps going: 90 rounds, 2 s apart.
    await until(() => fetchCount >= 3, { what: 'the poll to start' });
    await sleepReal(2100);

    root.unmount();
    roots.splice(0);
    const afterUnmount = fetchCount;
    await sleepReal(4500);          // two more rounds would have landed
    expect(fetchCount, 'Coverage kept polling after unmount').toBe(afterUnmount);
    expect(Array.from(liveTimers), 'Coverage left a timer armed').toEqual([]);
  });
});

describe('Logs stops its own timers when it unmounts', () => {
  it('clears the pending flush instead of writing to a dead page', async () => {
    const { root } = await render(LogsPage);
    const es = await until(() => streams[0], { what: 'the log stream' });

    // One line arms the 200 ms flush.
    es.emit('log', JSON.stringify({ level: 'INFO', logger_name: 'x', message: 'hello' }));
    await until(() => liveTimers.size > 0, { what: 'the flush timer' });

    root.unmount();
    roots.splice(0);
    expect(Array.from(liveTimers), 'Logs left its flush timer armed').toEqual([]);
  });
});
