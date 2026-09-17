// @vitest-environment jsdom
//
// #564: the server now judges each file by its latest attempt, so a requeue no
// longer has to delete the old history row to get it out of Issues. Deleting
// it was also wrong: DELETE /api/queue/scan/{id} drops the whole scan, and a
// scan can hold several files.
import { describe, it, expect, beforeAll, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

let QueuePage;
let calls;

function loadVendoredReact() {
  for (const name of ['react.production.min.js', 'react-dom.production.min.js']) {
    const url = path.resolve(process.cwd(), 'src/subarr/static/v1/vendor', name);
    (0, eval)(fs.readFileSync(url, 'utf8'));
  }
}

beforeAll(async () => {
  loadVendoredReact();
  ({ QueuePage } = await import('../queue.jsx'));
});

const FAILED = 'TV/Flics/Season 2/Flics - S02E03.mkv';
const RETRIED = 'TV/Flics/Season 2/Flics - S02E04.mkv';

const queuePayload = {
  processing: [], queued: [], idle: true,
  history: [
    {
      scan_id: 'scan-a', created_at: Date.now() / 1000 - 60, scan_status: 'done', path: FAILED,
      outcome: { category: 'skipped', label: 'skipped', detail: 'subgen skipped 1', skip_reason: 'unknown' },
    },
    {
      scan_id: 'scan-b', created_at: Date.now() / 1000 - 30, scan_status: 'done', path: RETRIED,
      outcome: { category: 'ok', label: 'completed', detail: 'transcribed' }, retries: 2,
    },
  ],
  history_counts: { ok: 1, skipped: 1, error: 0, running: 0, orphaned: 0 },
};

const reply = (status, body = {}) => ({
  ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body),
});

beforeEach(() => {
  calls = [];
  const stub = async (url, opts = {}) => {
    const u = String(url);
    calls.push({ url: u, method: (opts.method || 'GET').toUpperCase() });
    if (u === '/api/queue') return reply(200, queuePayload);
    if (u.startsWith('/api/queue/requeue')) return reply(202, { status: 'pending' });
    if (u.startsWith('/api/queue/pending')) return reply(200, { jobs: [], counts: {} });
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
});

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function until(pred, { timeout = 4000, what = 'condition' } = {}) {
  const t0 = Date.now();
  for (;;) {
    const v = pred();
    if (v) return v;
    if (Date.now() - t0 > timeout) throw new Error(`timed out waiting for ${what}`);
    await sleep(10);
  }
}

async function renderQueue() {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container);
  roots.push(root);
  root.render(React.createElement(QueuePage));
  await until(() => container.textContent.includes(FAILED), { what: 'the history rows' });
  return container;
}

describe('Queue requeue (#564)', () => {
  it('requeues without deleting the old scan', async () => {
    const container = await renderQueue();
    const btn = container.querySelector(`button[aria-label="Requeue ${FAILED}"]`);
    expect(btn).not.toBeNull();
    btn.click();
    await until(() => calls.some((c) => c.url === '/api/queue/requeue' && c.method === 'POST'),
      { what: 'the requeue POST' });
    await sleep(50);
    expect(calls.filter((c) => c.method === 'DELETE')).toEqual([]);
  });

  it('marks a row that replaced earlier failed attempts', async () => {
    const container = await renderQueue();
    const marker = container.querySelector(`[data-retries-for="${RETRIED}"]`);
    expect(marker).not.toBeNull();
    expect(marker.textContent).toMatch(/2 earlier attempts/);
    expect(container.querySelector(`[data-retries-for="${FAILED}"]`)).toBeNull();
  });
});
