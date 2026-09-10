// @vitest-environment jsdom
//
// #517: the FIRST component-level test in this repo.
//
// Every other frontend test here exercises exported helpers, and that gap had
// teeth. A reviewer mutated review.jsx so that fetchPending sent
// `grouped: false`, the page used the legacy pagination helper, and
// arrangeServerGroups was fed an empty payload. That is the whole #494 feature
// unwired in its only caller. 197 of 197 tests still passed.
//
// The helpers were exhaustively covered; the wiring that makes them run was
// covered by nothing. This file tests the seam between the component and the
// helpers, which is the part that can silently rot.
//
// React is NOT an npm dependency here. It is vendored in the repo at
// static/v1/vendor and served to browsers from there, so the test loads THAT
// file. Installing react from npm would let the tested React drift from the
// shipped one, which is the same class of mistake as testing a fixture instead
// of the artefact.
import { describe, it, expect, beforeAll, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

let ReviewPage;
let calls;

function loadVendoredReact() {
  for (const name of ['react.production.min.js', 'react-dom.production.min.js']) {
    // Resolved from the project root, NOT import.meta.url: under vitest that
    // URL is project-rooted, so converting it yields C:/src/... and the
    // failure reads like a missing vendor file rather than a bad path.
    const url = path.resolve(process.cwd(), 'src/subarr/static/v1/vendor', name);
    (0, eval)(fs.readFileSync(url, 'utf8'));
  }
}

beforeAll(async () => {
  loadVendoredReact();
  // Import AFTER real React is in place: a static import is hoisted and would
  // evaluate review.jsx against the stub.
  ({ ReviewPage } = await import('../review.jsx'));
});

beforeEach(() => {
  calls = [];
  const stub = (url, opts) => {
    calls.push(String(url));
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => ({
        count: 0, counts_by_flag: {}, flag: 'all', group_count: 0,
        limit: 25, offset: 0, page_count: 0, has_more: false,
        groups: [], items: [],
      }),
      text: async () => '{}',
    });
  };
  globalThis.fetch = stub;
  window.fetch = stub;
});

// Unmount so effect cleanups clear their timers before the jsdom window goes
// (see the note in review-bulk-failure-wiring.test.jsx).
const roots = [];
afterEach(() => {
  for (const r of roots.splice(0)) r.unmount();
  document.body.innerHTML = '';
});

async function renderReview() {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container);
  roots.push(root);
  root.render(React.createElement(ReviewPage));
  // let effects run and their promises settle
  for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0));
  return container;
}

describe('ReviewPage wiring (#517)', () => {
  it('actually asks the server for GROUPED pages', async () => {
    await renderReview();
    const pending = calls.filter((u) => u.includes('/api/audio-lang/pending-review'));
    expect(pending.length, `no pending-review request was made; calls: ${calls}`)
      .toBeGreaterThan(0);
    expect(
      pending.some((u) => u.includes('grouped=true')),
      `the page requested pending-review WITHOUT grouped=true: ${pending}`,
    ).toBe(true);
  });

  it('requests a group page size the server will accept', async () => {
    await renderReview();
    const pending = calls.filter((u) => u.includes('/api/audio-lang/pending-review'));
    const limits = pending
      .map((u) => Number(new URLSearchParams(u.split('?')[1] || '').get('limit')))
      .filter((n) => Number.isFinite(n) && n > 0);
    expect(limits.length).toBeGreaterThan(0);
    // Mirrors _GROUP_PAGE_MAX in routers/audio_lang.py, which 422s above it.
    for (const n of limits) expect(n).toBeLessThanOrEqual(100);
  });
});
