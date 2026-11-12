// @vitest-environment jsdom
//
// #666: a paused queue feed or coverage walk shows a neutral chip on its
// Health row, and its Run now button is disabled while it stays paused.
// React is vendored (static/v1/vendor), not an npm dependency.
import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

let TaskRow;
let HealthPage;

function loadVendoredReact() {
  for (const name of ['react.production.min.js', 'react-dom.production.min.js']) {
    const url = path.resolve(process.cwd(), 'src/subarr/static/v1/vendor', name);
    (0, eval)(fs.readFileSync(url, 'utf8'));
  }
}

beforeAll(async () => {
  loadVendoredReact();
  ({ TaskRow, HealthPage } = await import('../health.jsx'));
});

const reply = (status, body = {}) => ({
  ok: status < 400, status,
  json: async () => body,
  text: async () => JSON.stringify(body),
});

beforeEach(() => {
  const stub = async () => reply(200, {});
  globalThis.fetch = stub;
  window.fetch = stub;
});

const roots = [];
afterEach(() => {
  for (const r of roots.splice(0)) r.unmount();
  document.body.innerHTML = '';
});

async function settle() {
  for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0));
}

async function until(pred, { timeout = 4000, every = 10, what = 'condition' } = {}) {
  const t0 = Date.now();
  for (;;) {
    const v = pred();
    if (v) return v;
    if (Date.now() - t0 > timeout) throw new Error(`timed out waiting for ${what}`);
    await new Promise((r) => setTimeout(r, every));
  }
}

const base = (over = {}) => ({
  task_name: 'queue-feeder', can_run_now: true, is_unhealthy: false,
  expected_interval_s: 5, last_success_at: Math.floor(Date.now() / 1000),
  next_run_at: null, consecutive_failures: 0, last_error_detail: null,
  ...over,
});

const PAUSE = { label: 'feed paused', href: '/queue' };
const CHIP_TITLE = 'Paused. Open the page where you resume it.';
const RUN_TITLE = 'Paused. Resume it before running it now.';

async function renderTask(t, onRun = () => {}) {
  const c = document.createElement('div');
  document.body.appendChild(c);
  const root = ReactDOM.createRoot(c);
  roots.push(root);
  root.render(React.createElement(TaskRow, { t, version: 'test', onRun }));
  await settle();
  return c;
}

const chip = (c) => c.querySelector('[data-testid="task-paused"]');
const runButton = (c) => Array.from(c.querySelectorAll('button')).find((b) => /Run now/.test(b.textContent || ''));

describe('TaskRow paused chip (#666)', () => {
  it('shows no chip and an enabled Run now when paused is missing', async () => {
    const c = await renderTask(base());
    expect(chip(c)).toBeNull();
    const btn = runButton(c);
    expect(btn).toBeTruthy();
    expect(btn.disabled).toBe(false);
    expect(btn.getAttribute('title')).toBe('Trigger this job now');
  });

  it('shows no chip and an enabled Run now when paused is null', async () => {
    const c = await renderTask(base({ paused: null }));
    expect(chip(c)).toBeNull();
    const btn = runButton(c);
    expect(btn).toBeTruthy();
    expect(btn.disabled).toBe(false);
    expect(btn.getAttribute('title')).toBe('Trigger this job now');
  });

  it('shows the pause chip with its label, href and title', async () => {
    const c = await renderTask(base({ paused: PAUSE }));
    const a = chip(c);
    expect(a).toBeTruthy();
    expect(a.textContent).toBe('feed paused');
    expect(a.getAttribute('href')).toBe('/queue');
    expect(a.getAttribute('title')).toBe(CHIP_TITLE);
  });

  it('disables Run now while paused and ignores its click', async () => {
    const onRun = vi.fn();
    const c = await renderTask(base({ paused: PAUSE }), onRun);
    const btn = runButton(c);
    expect(btn).toBeTruthy();
    expect(btn.disabled).toBe(true);
    expect(btn.getAttribute('title')).toBe(RUN_TITLE);
    btn.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
    await settle();
    expect(onRun).not.toHaveBeenCalled();
  });

  it('keeps a healthy row reading healthy while paused', async () => {
    const c = await renderTask(base({ is_unhealthy: false, paused: PAUSE }));
    expect(c.textContent).toContain('healthy');
    expect(c.textContent).not.toContain('unhealthy');
  });

  it('keeps an unhealthy row reading unhealthy while paused', async () => {
    const c = await renderTask(base({ is_unhealthy: true, paused: PAUSE }));
    expect(c.textContent).toContain('unhealthy');
    expect(chip(c)).toBeTruthy();
  });

  it('shows the scheduler pause chip and no Run now button', async () => {
    const c = await renderTask(base({
      task_name: 'scheduler', can_run_now: false,
      paused: { label: 'coverage walk off', href: '/rules' },
    }));
    const a = chip(c);
    expect(a).toBeTruthy();
    expect(a.textContent).toBe('coverage walk off');
    expect(a.getAttribute('href')).toBe('/rules');
    expect(runButton(c)).toBeFalsy();
  });

  it('ignores invalid paused payloads', async () => {
    const values = [
      { label: '', href: '/queue' },
      { label: 'x' },
      { label: 'x', href: 'https://evil.test' },
      { label: 'x', href: '//evil.test' },
      { label: 'x', href: 'javascript:alert(1)' },
      { label: 5, href: '/queue' },
      'feed paused',
    ];
    for (const paused of values) {
      const c = await renderTask(base({ paused }));
      expect(chip(c)).toBeNull();
      const btn = runButton(c);
      expect(btn).toBeTruthy();
      expect(btn.disabled).toBe(false);
      expect(btn.getAttribute('title')).toBe('Trigger this job now');
    }
  });

  it('chip click does not toggle the traceback and the row toggle still works', async () => {
    const c = await renderTask(base({
      last_error_detail: 'Traceback (most recent call last): boom', paused: PAUSE,
    }));
    c.addEventListener('click', (e) => e.preventDefault(), true);
    const a = chip(c);
    expect(a).toBeTruthy();
    a.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
    await settle();
    expect(c.textContent).not.toContain('boom');
    const name = Array.from(c.querySelectorAll('span'))
      .find((s) => (s.textContent || '').startsWith('queue-feeder'));
    expect(name).toBeTruthy();
    name.click();
    await settle();
    expect(c.textContent).toContain('boom');
  });

  it('follows paused props on re-render in both directions', async () => {
    const c = await renderTask(base({ paused: PAUSE }));
    expect(chip(c)).toBeTruthy();
    expect(runButton(c).disabled).toBe(true);

    const root = roots[roots.length - 1];
    root.render(React.createElement(TaskRow, { t: base({ paused: null }), version: 'test', onRun: () => {} }));
    await settle();
    expect(chip(c)).toBeNull();
    expect(runButton(c).disabled).toBe(false);

    root.render(React.createElement(TaskRow, { t: base({ paused: PAUSE }), version: 'test', onRun: () => {} }));
    await settle();
    expect(chip(c)).toBeTruthy();
    expect(runButton(c).disabled).toBe(true);
  });

  it('calls onRun once from an unpaused Run now', async () => {
    const onRun = vi.fn(() => Promise.resolve(null));
    const c = await renderTask(base(), onRun);
    runButton(c).click();
    await settle();
    expect(onRun).toHaveBeenCalledTimes(1);
    expect(onRun).toHaveBeenCalledWith('queue-feeder');
  });

  it('renders both pause chips through the real HealthPage', async () => {
    const tasks = [
      base({ paused: PAUSE }),
      base({ task_name: 'scheduler', can_run_now: false, paused: { label: 'coverage walk off', href: '/rules' } }),
    ];
    const stub = async (url) => {
      if (String(url).includes('/api/health/tasks')) {
        return reply(200, { tasks, any_unhealthy: false, unhealthy_count: 0, version: '9.9.9' });
      }
      return reply(200, {});
    };
    globalThis.fetch = stub;
    window.fetch = stub;

    const c = document.createElement('div');
    document.body.appendChild(c);
    const root = ReactDOM.createRoot(c);
    roots.push(root);
    root.render(React.createElement(HealthPage));
    await until(() => c.querySelectorAll('[data-testid="task-paused"]').length === 2,
      { what: 'the two pause chips' });
    const chips = Array.from(c.querySelectorAll('[data-testid="task-paused"]'));
    expect(chips[0].textContent).toBe('feed paused');
    expect(chips[1].textContent).toBe('coverage walk off');
  });
});
