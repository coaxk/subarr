// @vitest-environment jsdom
//
// The page must leave NOTHING running after it is unmounted.
//
// Two costs, one cause. In CI, `frontend unit tests` failed on main on
// 2026-09-11 and 2026-09-15 with every test passing and one stray
// `ReferenceError: window is not defined`: a 350 ms minimum-spinner timer
// armed by fetchPending's `finally` fired after vitest tore the jsdom window
// down. Unmounting in afterEach did NOT stop it, because the timer is awaited
// inside a detached async function, not registered with React.
//
// In the app the same shape is worse: the re-probe poll sleeps 500 ms up to
// 600 times (five minutes) and the coverage-rebuild wait 40 times, each
// followed by a fetch and setState. Leaving Review mid-re-probe kept all of
// that running against a dead component.
//
// These tests assert the lifetime rule directly — no live timers, no fetches
// after unmount — rather than the flake's symptom, which is timing-dependent
// and would pass or fail by luck.
import { describe, it, expect, beforeAll, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

let ReviewPage;
let liveTimers;          // ids started by the page and not yet cleared or fired
let realSetTimeout;
let realClearTimeout;
let fetchCount;

function loadVendoredReact() {
  for (const name of ['react.production.min.js', 'react-dom.production.min.js']) {
    const url = path.resolve(process.cwd(), 'src/subarr/static/v1/vendor', name);
    (0, eval)(fs.readFileSync(url, 'utf8'));
  }
}

beforeAll(async () => {
  loadVendoredReact();
  ({ ReviewPage } = await import('../review.jsx'));
});

const reply = (status, body = {}) => ({
  ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body),
});

const rows = (n) => Array.from({ length: n }, (_, i) => ({
  group_key: 'g1', title: 'Show', media_type: 'show', library: 'TV', flag: 'suspect',
  file_canonical_path: `TV/Show/S01E0${i + 1}.mkv`, canonical_path: `/TV/Show/S01E0${i + 1}.mkv`,
  episode_number: String(i + 1),
}));

const pendingBody = (n = 2) => ({
  count: n, counts_by_flag: { suspect: n }, flag: 'all', group_count: 1, limit: 25,
  offset: 0, page_count: 1, has_more: false,
  groups: [{ key: 'g1', title: 'Show', media_type: 'show', library: 'TV', canonical_root: '/tv/' }],
  items: rows(n),
});

beforeEach(() => {
  fetchCount = 0;
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

  const stub = async (url) => {
    fetchCount += 1;
    const u = String(url);
    if (u.includes('/api/audio-lang/pending-review')) return reply(200, pendingBody());
    if (u.includes('/api/probe/reprobe')) return reply(200, { id: 'w1', status: 'running', processed: 0, total_files: 2 });
    if (u.includes('/api/probe/walk/')) return reply(200, { id: 'w1', status: 'running', processed: 1, total_files: 2 });
    if (u.includes('/api/coverage/status')) return reply(200, { generated_at: 'same' });
    return reply(200, {});
  };
  globalThis.fetch = stub; window.fetch = stub;
  window.confirm = () => true;
  window.alert = () => {};
});

afterEach(() => {
  for (const r of roots.splice(0)) r.unmount();
  document.body.innerHTML = '';
  globalThis.setTimeout = realSetTimeout;
  globalThis.clearTimeout = realClearTimeout;
  window.setTimeout = realSetTimeout;
  window.clearTimeout = realClearTimeout;
});

const roots = [];

async function until(pred, { timeout = 4000, every = 10, what = 'condition' } = {}) {
  const t0 = Date.now();
  for (;;) {
    const v = pred();
    if (v) return v;
    if (Date.now() - t0 > timeout) throw new Error(`timed out waiting for ${what}`);
    await new Promise((r) => realSetTimeout(r, every));
  }
}

const sleepReal = (ms) => new Promise((r) => realSetTimeout(r, ms));

async function renderReview() {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container);
  roots.push(root);
  root.render(React.createElement(ReviewPage));
  await until(() => container.querySelector('input[aria-label^="Select all"]'),
    { what: 'the group row to render' });
  return { container, root };
}

const buttonMatching = (c, re) => Array.from(c.querySelectorAll('button')).find((b) => re.test(b.textContent || ''));

describe('Review stops everything it started when it unmounts', () => {
  it('leaves no live timer behind', async () => {
    const { root } = await renderReview();
    // The first fetch resolves in well under the 350 ms minimum-spinner
    // window, so fetchPending's padding timer is armed right now.
    await until(() => liveTimers.size > 0, { what: 'the page to arm a timer' });
    root.unmount();
    roots.splice(0);
    expect(Array.from(liveTimers)).toEqual([]);
  });

  it('stops the re-probe polling instead of fetching against a dead page', async () => {
    const { container, root } = await renderReview();
    container.querySelector('input[aria-label^="Select all"]').click();
    const reprobe = await until(() => buttonMatching(container, /^Re-probe \(\d+\)/),
      { what: 'the Re-probe button' });
    reprobe.click();
    // Wait for the poll loop to be underway: the walk endpoint has answered at
    // least once and the next 500 ms sleep is armed.
    await until(() => /Re-probing/.test(container.textContent || ''), { what: 'the re-probe to start' });
    await sleepReal(700);

    root.unmount();
    roots.splice(0);
    const afterUnmount = fetchCount;
    await sleepReal(1500);   // three more poll ticks would have fired by now
    expect(fetchCount, 'the page kept fetching after unmount').toBe(afterUnmount);
    expect(Array.from(liveTimers), 'the page left a timer armed').toEqual([]);
  });
});
