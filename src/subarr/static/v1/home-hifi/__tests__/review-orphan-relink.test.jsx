// @vitest-environment jsdom
//
// #638: the OrphanBanner offers one card per path-rewrite rule above the prune
// text. These tests drive the REAL ReviewPage through the vendored-React
// harness (same pattern as review-bulk-failure-wiring.test.jsx) with fetch
// stubbed at the network edge only. `srv` is server truth: a relink POST
// mutates it, and what the banner shows afterwards must come from the
// re-fetched GETs, never from the POST body.
import { describe, it, expect, beforeAll, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

let ReviewPage;
let srv;
let calls;     // "METHOD url", in order

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
  ok: status < 400, status,
  json: async () => body,
  text: async () => JSON.stringify(body),
});

const pair = (i, how = 'size') => ({
  old: `Old/Show${i}/S1/e${i}.mkv`, new: `Show${i}/S1/e${i}.mkv`, how,
});

const rule = (over = {}) => ({
  old_head: 'Old', new_head: '', verified: 4, by_size: 4, path_only: 1,
  path_only_withheld: 0, mismatches: 0, collisions: 0, truncated: 0,
  sample: [pair(0), pair(1), pair(2), pair(3), pair(4, 'path_only')],
  pairs_by_size: [pair(0), pair(1), pair(2), pair(3)],
  pairs_path_only: [pair(4, 'path_only')],
  ...over,
});

const relinkBody = (rules) => ({
  rules, stale: 5, inventory: { files: 9, unreadable_libraries: [], walk_s: 0.1 },
});

beforeEach(() => {
  calls = [];
  srv = {
    orphans: { would_delete: 5, safe: true, reason: 'ok', missing: ['Old/Show0/S1/e0.mkv'] },
    relink: relinkBody([rule()]),   // null = the relink endpoint fails
    relinkPostResult: { applied: true, reason: 'ok', relinked: 4, skipped_collision: 0, failed: 0 },
    onRelinkPost: null,             // (body) => void, mutates srv
    pruneResult: { safe: true, deleted_total: 1, reason: 'ok' },
    relinkPostHold: null,           // a promise the POST waits on
  };
  const stub = async (url, opts = {}) => {
    const u = String(url);
    const method = (opts.method || 'GET').toUpperCase();
    calls.push(`${method} ${u}`);
    if (u.includes('/api/audio-lang/pending-review')) {
      return reply(200, {
        count: 0, counts_by_flag: {}, flag: 'all', group_count: 0, limit: 25, offset: 0,
        page_count: 1, has_more: false, groups: [], items: [],
      });
    }
    if (u.includes('/api/admin/db/orphans/relink')) {
      if (method === 'POST') {
        const body = JSON.parse(opts.body);
        srv.postedBody = body;
        if (srv.relinkPostHold) await srv.relinkPostHold;
        if (srv.onRelinkPost) srv.onRelinkPost(body);
        return reply(200, srv.relinkPostResult);
      }
      if (srv.relink === null) return reply(500, { detail: 'boom' });
      return reply(200, srv.relink);
    }
    if (u.includes('/api/admin/db/orphans/prune')) {
      srv.pruned = true;
      srv.orphans = { would_delete: 0, safe: true, reason: 'ok', missing: [] };
      return reply(200, srv.pruneResult);
    }
    if (u.includes('/api/admin/db/orphans')) return reply(200, srv.orphans);
    return reply(200, {});
  };
  globalThis.fetch = stub;
  window.fetch = stub;
  window.confirm = () => true;
  window.alert = () => {};
});

const roots = [];
afterEach(() => {
  for (const r of roots.splice(0)) r.unmount();
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

const text = (c) => c.textContent || '';
const buttonMatching = (c, re) => Array.from(c.querySelectorAll('button')).find((b) => re.test(b.textContent || ''));
const buttonsMatching = (c, re) => Array.from(c.querySelectorAll('button')).filter((b) => re.test(b.textContent || ''));

async function renderReview() {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container);
  roots.push(root);
  root.render(React.createElement(ReviewPage));
  await until(() => /Review/.test(text(container)), { what: 'the page to render' });
  return container;
}

describe('OrphanBanner relink cards (#638)', () => {
  it('renders a relink card from the dry run', async () => {
    const c = await renderReview();
    await until(() => /Relink all/.test(text(c)), { what: 'the relink card' });
    const t = text(c);
    expect(t).toContain('Old → (library root)');
    expect(t).toContain('4 matched by path + size, 1 by path only');
    // the sample pairs are visible without expanding
    expect(t).toContain('Old/Show0/S1/e0.mkv');
    expect(t).toContain('Show0/S1/e0.mkv');
    expect(buttonMatching(c, /^Skip$/)).toBeTruthy();
    expect(t).not.toContain('held back');
  });

  it('names a non-empty new head and the held-back path-only rows', async () => {
    srv.relink = relinkBody([rule({ old_head: '@disk2/Old', new_head: '@disk3', path_only: 0, path_only_withheld: 2, mismatches: 1 })]);
    const c = await renderReview();
    await until(() => /Relink all/.test(text(c)), { what: 'the relink card' });
    const t = text(c);
    expect(t).toContain('@disk2/Old → @disk3');
    expect(t).toContain('(2 path-only rows held back: a size did not match)');
  });

  it('renders the card while the prune is blocked', async () => {
    srv.orphans = { would_delete: 15000, safe: false, reason: 'Too many rows are missing at once.', missing: [] };
    const c = await renderReview();
    await until(() => /Relink all/.test(text(c)), { what: 'the relink card' });
    const t = text(c);
    expect(t).toContain('cleanup is on hold');
    expect(t).toContain('Old → (library root)');
    // no prune button is offered while blocked
    expect(buttonMatching(c, /^Remove \d+ row/)).toBeFalsy();
  });

  it('renders the card above the prune text', async () => {
    const c = await renderReview();
    await until(() => /Relink all/.test(text(c)), { what: 'the relink card' });
    const t = text(c);
    expect(t.indexOf('Relink all')).toBeLessThan(t.indexOf('reference files that no longer exist'));
  });

  it('Show all lists both sections and says how many were truncated', async () => {
    srv.relink = relinkBody([rule({ truncated: 7 })]);
    const c = await renderReview();
    await until(() => /Relink all/.test(text(c)), { what: 'the relink card' });
    expect(text(c)).not.toContain('Matched by path only');
    buttonMatching(c, /^Show all$/).click();
    await until(() => /Matched by path only/.test(text(c)), { what: 'the expanded lists' });
    const t = text(c);
    expect(t).toContain('Matched by path + size');
    expect(t).toContain('and 7 more');
    expect(buttonMatching(c, /^Hide$/)).toBeTruthy();
  });

  it('Relink all posts the rule and re-fetches, banner follows the server', async () => {
    srv.onRelinkPost = () => {
      srv.relink = relinkBody([]);
      srv.orphans = { would_delete: 1, safe: true, reason: 'ok', missing: ['Old/Gone/S1/g.mkv'] };
    };
    const c = await renderReview();
    const btn = await until(() => buttonMatching(c, /^Relink all$/), { what: 'Relink all' });
    const before = calls.length;
    btn.click();
    await until(() => /Relinked 4/.test(text(c)), { what: 'the result line' });
    await until(() => !/Relink all/.test(text(c)), { what: 'the card to go' });

    expect(srv.postedBody).toEqual({ old_head: 'Old', new_head: '' });
    const after = calls.slice(before);
    const postAt = after.findIndex((x) => x.startsWith('POST') && x.includes('/orphans/relink'));
    expect(postAt).toBeGreaterThanOrEqual(0);
    const tail = after.slice(postAt + 1);
    // BOTH reads happen again after the POST
    expect(tail.some((x) => x === 'GET /api/admin/db/orphans')).toBe(true);
    expect(tail.some((x) => x === 'GET /api/admin/db/orphans/relink')).toBe(true);
    // The remainder is now under the guard and the normal offer is there.
    expect(text(c)).toMatch(/1 row reference files that no longer exist/);
    buttonMatching(c, /^Show these$/).click();
    await until(() => buttonMatching(c, /^Remove 1 row$/), { what: 'the Remove 1 row offer' });
  });

  it('the banner follows the server, not the POST echo', async () => {
    // The POST claims success, but the server still offers the rule.
    const c = await renderReview();
    (await until(() => buttonMatching(c, /^Relink all$/), { what: 'Relink all' })).click();
    await until(() => /Relinked 4/.test(text(c)), { what: 'the result line' });
    await until(() => calls.filter((x) => x === 'GET /api/admin/db/orphans/relink').length >= 2, { what: 'the re-fetch' });
    expect(buttonMatching(c, /^Relink all$/)).toBeTruthy();
  });

  it('the relink result line survives a relink that clears every stale row', async () => {
    srv.onRelinkPost = () => {
      srv.relink = relinkBody([]);
      srv.orphans = { would_delete: 0, safe: true, reason: 'ok', missing: [] };
    };
    const c = await renderReview();
    (await until(() => buttonMatching(c, /^Relink all$/), { what: 'Relink all' })).click();
    await until(() => /Relinked 4/.test(text(c)), { what: 'the result line' });
    await until(() => !/Relink all/.test(text(c)), { what: 'the card to go' });
    expect(text(c)).toMatch(/Relinked 4/);
    // still there after everything settled
    await new Promise((r) => setTimeout(r, 60));
    expect(text(c)).toMatch(/Relinked 4/);
    expect(buttonMatching(c, /Dismiss|×/)).toBeTruthy();
  });

  it('reports non-zero skipped and failed counts', async () => {
    srv.relinkPostResult = { applied: true, reason: 'ok', relinked: 2, skipped_collision: 1, failed: 3 };
    const c = await renderReview();
    (await until(() => buttonMatching(c, /^Relink all$/), { what: 'Relink all' })).click();
    await until(() => /Relinked 2/.test(text(c)), { what: 'the result line' });
    expect(text(c)).toMatch(/1 skipped \(already verified at the new path\)/);
    expect(text(c)).toMatch(/3 failed/);
  });

  it('shows the reason when the rule is no longer offered', async () => {
    srv.relinkPostResult = { applied: false, reason: 'this rewrite is no longer offered', relinked: 0, skipped_collision: 0, failed: 0 };
    const c = await renderReview();
    (await until(() => buttonMatching(c, /^Relink all$/), { what: 'Relink all' })).click();
    await until(() => /no longer offered/.test(text(c)), { what: 'the reason' });
    expect(text(c)).not.toMatch(/Relinked 0/);
  });

  it('disables Relink all while the POST is in flight', async () => {
    let release;
    srv.relinkPostHold = new Promise((r) => { release = r; });
    const c = await renderReview();
    const btn = await until(() => buttonMatching(c, /^Relink all$/), { what: 'Relink all' });
    btn.click();
    await until(() => buttonMatching(c, /Relinking/), { what: 'the busy label' });
    expect(buttonMatching(c, /Relinking/).disabled).toBe(true);
    release();
    await until(() => /Relinked 4/.test(text(c)), { what: 'the result line' });
  });

  it('Skip hides only that card', async () => {
    srv.relink = relinkBody([
      rule({ old_head: 'OldA', new_head: '' }),
      rule({ old_head: 'OldB', new_head: 'New' }),
    ]);
    const c = await renderReview();
    await until(() => buttonsMatching(c, /^Relink all$/).length === 2, { what: 'two cards' });
    buttonsMatching(c, /^Skip$/)[0].click();
    await until(() => buttonsMatching(c, /^Relink all$/).length === 1, { what: 'one card' });
    const t = text(c);
    expect(t).not.toContain('OldA →');
    expect(t).toContain('OldB → New');
  });

  it('a failed relink fetch shows no card and the prune banner still works', async () => {
    srv.relink = null;
    srv.orphans = { would_delete: 2, safe: true, reason: 'ok', missing: ['a.mkv', 'b.mkv'] };
    const c = await renderReview();
    await until(() => /2 rows reference files that no longer exist/.test(text(c)), { what: 'the prune text' });
    expect(text(c)).not.toContain('Relink all');
    buttonMatching(c, /^Show these$/).click();
    (await until(() => buttonMatching(c, /^Remove 2 rows$/), { what: 'Remove 2 rows' })).click();
    await until(() => srv.pruned, { what: 'the prune POST' });
    await until(() => /Removed 1 orphaned row/.test(text(c)), { what: 'the prune result' });
  });

  it('a relink fetch that throws is silent', async () => {
    const inner = globalThis.fetch;
    const stub = async (url, opts) => {
      if (String(url).includes('/orphans/relink')) throw new Error('offline');
      return inner(url, opts);
    };
    globalThis.fetch = stub;
    window.fetch = stub;
    const c = await renderReview();
    await until(() => /reference files that no longer exist/.test(text(c)), { what: 'the prune text' });
    expect(text(c)).not.toContain('Relink all');
  });

  it('with nothing to prune and no rules the banner stays hidden', async () => {
    srv.orphans = { would_delete: 0, safe: true, reason: 'ok', missing: [] };
    srv.relink = relinkBody([]);
    const c = await renderReview();
    await until(() => calls.some((x) => x.includes('/orphans/relink')), { what: 'the relink fetch' });
    await new Promise((r) => setTimeout(r, 60));
    expect(text(c)).not.toContain('Relink');
    expect(text(c)).not.toContain('no longer exist');
  });

  it('a rule alone (nothing else to prune) still shows its card', async () => {
    srv.orphans = { would_delete: 0, safe: true, reason: 'ok', missing: [] };
    const c = await renderReview();
    await until(() => /Relink all/.test(text(c)), { what: 'the relink card' });
  });

  it('Dismiss hides the whole banner', async () => {
    const c = await renderReview();
    await until(() => /Relink all/.test(text(c)), { what: 'the relink card' });
    c.querySelector('button[aria-label="Dismiss"]').click();
    await until(() => !/Relink all/.test(text(c)), { what: 'the banner to go' });
  });
});
