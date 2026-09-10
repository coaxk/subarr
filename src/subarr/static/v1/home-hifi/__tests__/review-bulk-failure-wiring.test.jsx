// @vitest-environment jsdom
//
// #515: bulk verify failures were counted, returned, and then destroyed by
// the batch's own completion. The count was only ever rendered inside
// `{bulkRunning && ...}` inside `{selectedCount > 0 && ...}`, and `finish`
// falsified both in one commit. A user who bulk-assigned 400 episodes against
// an expired Sonarr key saw the bar flash "400 failed" and vanish, then the
// same 400 rows, and nothing else.
//
// These tests drive the REAL component through the #517 jsdom harness: tick a
// whole group, click Apply, answer the confirm, and let real fetches (stubbed
// at the network edge only) succeed or 401. What is asserted is what the user
// can see AFTER the batch has finished - the state the old code threw away.
import { describe, it, expect, beforeAll, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

let ReviewPage;
let calls;      // every fetched URL, in order
let posts;      // canonical_path of every verification POST, in order
let net;        // per-test network policy

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

const rowPath = (i) => `TV/Show/S01E${String(i + 1).padStart(2, '0')}.mkv`;

function makeNet({ rows = 3, verify }) {
  // `verified` is server truth: a row whose POST succeeded leaves the pending
  // list on the next fetch, exactly as the confirm text promises. A row whose
  // POST failed stays. The test never has to fake the refetch result by hand.
  const verified = new Set();
  const all = Array.from({ length: rows }, (_, i) => rowPath(i));
  return {
    all,
    verified,
    verify,   // (canonical_path) => Promise<{status}>; decides each POST
    pending() {
      const items = all.filter((p) => !verified.has(p)).map((p, i) => ({
        group_key: 'g1', title: 'Show', media_type: 'show', library: 'TV',
        flag: 'suspect', file_canonical_path: p, canonical_path: `/${p}`,
        episode_number: String(i + 1),
      }));
      const groups = items.length
        ? [{ key: 'g1', title: 'Show', media_type: 'show', library: 'TV', canonical_root: '/tv/' }]
        : [];
      return {
        count: items.length, counts_by_flag: { suspect: items.length }, flag: 'all',
        group_count: groups.length, limit: 25, offset: 0, page_count: 1, has_more: false,
        groups, items,
      };
    },
  };
}

const reply = (status, body = {}) => ({
  ok: status < 400, status,
  json: async () => body,
  text: async () => JSON.stringify(body),
});

beforeEach(() => {
  calls = [];
  posts = [];
  net = null;
  const stub = async (url, opts = {}) => {
    const u = String(url);
    calls.push(u);
    if (u.includes('/api/audio-lang/pending-review')) return reply(200, net.pending());
    if (u.includes('/api/audio-lang/verifications') && opts.method === 'POST') {
      const body = JSON.parse(opts.body);
      posts.push(body.canonical_path);
      const r = await net.verify(body.canonical_path);
      if (r.status < 400) net.verified.add(body.canonical_path);
      return reply(r.status, r.body || {});
    }
    if (u.includes('/api/audio-lang/series-intent')) return reply(200, {});
    if (u.includes('/api/admin/db/orphans')) return reply(200, { would_delete: 0, safe: true });
    return reply(200, {});
  };
  globalThis.fetch = stub;
  window.fetch = stub;
  window.confirm = () => true;
  window.alert = () => {};
});

afterEach(() => {
  document.body.innerHTML = '';
});

async function until(pred, { timeout = 4000, every = 10, what = 'condition' } = {}) {
  const t0 = Date.now();
  for (;;) {
    const v = pred();
    if (v) return v;
    if (Date.now() - t0 > timeout) throw new Error(`timed out waiting for ${what}`);
    await new Promise((r) => setTimeout(r, every));
  }
}

async function renderReview() {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container);
  root.render(React.createElement(ReviewPage));
  await until(() => container.querySelector('input[aria-label^="Select all"]'),
    { what: 'the group row to render' });
  return container;
}

const text = (c) => c.textContent || '';
const buttonMatching = (c, re) => Array.from(c.querySelectorAll('button')).find((b) => re.test(b.textContent || ''));

async function selectAllAndApply(container) {
  container.querySelector('input[aria-label^="Select all"]').click();
  await until(() => /\d+ files? selected/.test(text(container)), { what: 'the selection bar' });
  const apply = await until(() => buttonMatching(container, /^Apply to \d+/), { what: 'the Apply button' });
  apply.click();
}

async function batchFinished(container) {
  // The batch is over when the primary button stops reading "Applying…" (or
  // the bar is gone entirely) AND no verification POST is still pending.
  await until(() => !/Applying…/.test(text(container)), { what: 'the batch to finish' });
  // let the post-batch refetch and its 350 ms minimum-spinner padding settle
  await until(() => !container.querySelector('.spinner-ring'), { what: 'the refetch spinner to clear' });
}

describe('bulk verify failure surface (#515)', () => {
  it('a partial failure stays visible after the batch ends, and only the failed row stays selected', async () => {
    net = makeNet({
      rows: 3,
      verify: async (p) => (p === rowPath(1) ? { status: 401, body: { detail: 'unauthorized' } } : { status: 200 }),
    });
    const c = await renderReview();
    await selectAllAndApply(c);
    await batchFinished(c);

    expect(posts).toHaveLength(3);
    const t = text(c);
    // The failure is still on screen after completion - the exact thing the
    // old code destroyed.
    expect(t, `no failure surface after the batch: ${t}`).toMatch(/1 of 3 failed/);
    // It names the file, so the user does not have to hunt the list for the
    // row that "did not leave".
    expect(t).toContain(rowPath(1));
    // Failed rows stay ticked; the two that succeeded left the list.
    expect(t).toMatch(/1 file selected/);
    expect(t).not.toContain(rowPath(0));
    expect(t).not.toContain(rowPath(2));
  });

  it('when every POST fails (expired API key) the whole selection survives and the bar stays mounted', async () => {
    net = makeNet({ rows: 3, verify: async () => ({ status: 401 }) });
    const c = await renderReview();
    await selectAllAndApply(c);
    await batchFinished(c);

    expect(posts).toHaveLength(3);
    const t = text(c);
    expect(t).toMatch(/3 of 3 failed/);
    expect(t).toMatch(/3 files selected/);
    // The user can retry from where they were: the action is still offered.
    expect(buttonMatching(c, /^Apply to 3/)).toBeTruthy();
  });

  it('a clean batch shows no failure surface and clears the selection as before', async () => {
    net = makeNet({ rows: 3, verify: async () => ({ status: 200 }) });
    const c = await renderReview();
    await selectAllAndApply(c);
    await batchFinished(c);

    expect(posts).toHaveLength(3);
    const t = text(c);
    expect(t).not.toMatch(/failed/);
    expect(t).not.toMatch(/files? selected/);
  });

  it('a failure surface can be dismissed', async () => {
    net = makeNet({ rows: 2, verify: async () => ({ status: 500 }) });
    const c = await renderReview();
    await selectAllAndApply(c);
    await batchFinished(c);
    expect(text(c)).toMatch(/2 of 2 failed/);
    const dismiss = await until(() => c.querySelector('[role="status"] button[aria-label="Dismiss"]'),
      { what: 'the dismiss control' });
    dismiss.click();
    await until(() => !/2 of 2 failed/.test(text(c)), { what: 'the surface to go' });
  });
});

// #516: a 200 does not mean the write landed. The local verification persists
// and the response carries `sonarr_propagation: {attempted, ok, reason, detail}`;
// every caller read only `r.ok`, so 400 files could leave Review "successfully"
// while Sonarr still recorded English for all of them.
describe('propagation failures inside a 200 are surfaced (#516)', () => {
  const unresolved = {
    attempted: true, ok: false, reason: 'episode_file_unresolved',
    detail: "couldn't resolve this file's Sonarr episodeFile id from the coverage snapshot",
  };

  it('names how many files Sonarr was not updated for, and why, after a batch every POST accepted', async () => {
    net = makeNet({ rows: 3, verify: async () => ({ status: 200, body: { verified: true, sonarr_propagation: unresolved } }) });
    const c = await renderReview();
    await selectAllAndApply(c);
    await batchFinished(c);

    expect(posts).toHaveLength(3);
    const t = text(c);
    // The rows DID verify locally, so they leave the list and the selection
    // clears - this is not a failure of the batch...
    expect(t).not.toMatch(/of 3 failed/);
    expect(t).not.toMatch(/files? selected/);
    // ...but the user must learn that Sonarr was not updated, once, with the
    // reason, not 400 times or never.
    expect(t, `no propagation surface: ${t}`).toMatch(/Sonarr was not updated for 3 files/);
    expect(t).toContain('coverage snapshot');
    expect((t.match(/coverage snapshot/g) || []).length).toBe(1);
  });

  it('a clean propagation shows nothing', async () => {
    net = makeNet({ rows: 2, verify: async () => ({ status: 200, body: { verified: true, sonarr_propagation: { attempted: true, ok: true } } }) });
    const c = await renderReview();
    await selectAllAndApply(c);
    await batchFinished(c);
    expect(text(c)).not.toMatch(/Sonarr was not updated/);
  });
});

describe('bulk verify is interruptible and guarded (#515)', () => {
  // Hold every verification POST open until the test releases it, so the
  // batch is observably "running" for as long as the assertion needs.
  function heldNet(rows) {
    const holds = [];
    const n = makeNet({
      rows,
      verify: (p) => new Promise((resolve) => { holds.push({ p, resolve }); }),
    });
    n.release = (status = 200) => { for (const h of holds.splice(0)) h.resolve({ status }); };
    n.holds = holds;
    return n;
  }

  it('warns before unload while a batch is running, and not once it has finished', async () => {
    net = heldNet(3);
    const c = await renderReview();
    await selectAllAndApply(c);
    await until(() => net.holds.length >= 1, { what: 'the first POST to be in flight' });

    const during = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(during);
    expect(during.defaultPrevented, 'navigation mid-batch was not guarded').toBe(true);

    net.release(200);
    await until(() => posts.length === 3, { what: 'all three POSTs' });
    net.release(200);
    await batchFinished(c);

    const after = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(after);
    expect(after.defaultPrevented, 'guard left armed after the batch').toBe(false);
  });

  it('Stop finishes in-flight requests, starts nothing else, reports what was not attempted, and leaves those rows selected', async () => {
    net = heldNet(6);   // 4 workers: four start at once, two are queued
    const c = await renderReview();
    await selectAllAndApply(c);
    await until(() => net.holds.length === 4, { what: 'four POSTs in flight' });

    const stop = await until(() => buttonMatching(c, /^Stop/), { what: 'the Stop button' });
    stop.click();
    net.release(200);
    await batchFinished(c);

    // Exactly the four that were already in flight were sent; the two queued
    // behind them were never started.
    expect(posts).toHaveLength(4);
    const t = text(c);
    expect(t).toMatch(/Stopped/);
    expect(t).toMatch(/2 not attempted/);
    // The four that completed left the list; the two untouched rows stay ticked
    // so the user can resume with one click - and nothing that succeeded is
    // named as a failure.
    expect(t).toMatch(/2 files selected/);
    expect(t).toMatch(/Apply again to resume/);
    for (const p of net.all.slice(0, 4)) expect(t).not.toContain(p);
  });
});
