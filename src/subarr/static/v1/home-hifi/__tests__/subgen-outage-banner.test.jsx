// @vitest-environment jsdom
//
// #479: the persistent outage banner on the dashboard, driven through the
// vendored-React harness (#517).
import { describe, it, expect, beforeAll, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

let SubgenOutageBanner;

function loadVendoredReact() {
  for (const name of ['react.production.min.js', 'react-dom.production.min.js']) {
    const url = path.resolve(process.cwd(), 'src/subarr/static/v1/vendor', name);
    (0, eval)(fs.readFileSync(url, 'utf8'));
  }
}

beforeAll(async () => {
  loadVendoredReact();
  ({ SubgenOutageBanner } = await import('../subgen-outage-banner.jsx'));
});

const roots = [];
beforeEach(() => {
  try { window.localStorage.clear(); } catch { /* jsdom without storage */ }
});
afterEach(() => {
  for (const r of roots.splice(0)) r.unmount();
  document.body.innerHTML = '';
});

async function until(pred, { timeout = 3000, every = 10, what = 'condition' } = {}) {
  const t0 = Date.now();
  for (;;) {
    const v = pred();
    if (v) return v;
    if (Date.now() - t0 > timeout) throw new Error(`timed out waiting for ${what}`);
    await new Promise((r) => setTimeout(r, every));
  }
}

async function render(outage) {
  const c = document.createElement('div');
  document.body.appendChild(c);
  const root = ReactDOM.createRoot(c);
  roots.push(root);
  root.render(React.createElement(SubgenOutageBanner, { outage }));
  await new Promise((r) => setTimeout(r, 20));
  return c;
}

const OUTAGE = { since: 1700000000, seconds: 86400 * 2 + 3600, cause: 'refused', consecutive: 5000, hint: 'Check the port and that subgen is running.' };

describe('SubgenOutageBanner (#479)', () => {
  it('renders the duration, cause, consequence and hint', async () => {
    const c = await render(OUTAGE);
    const t = c.textContent;
    expect(t).toContain('unreachable for 2 days 1 hour');
    expect(t).toContain('refused');
    expect(t).toContain('Nothing is being transcribed');
    expect(t).toContain('Check the port');
  });

  it('renders nothing for a short blip or no outage', async () => {
    expect((await render({ ...OUTAGE, seconds: 120 })).textContent).toBe('');
    expect((await render(null)).textContent).toBe('');
  });

  it('dismiss hides this outage and stays hidden on re-render; a new outage shows again', async () => {
    const c = await render(OUTAGE);
    const btn = await until(() => c.querySelector('button[aria-label="Dismiss"]'), { what: 'dismiss' });
    btn.click();
    await until(() => c.textContent === '', { what: 'the banner to go' });
    expect((await render(OUTAGE)).textContent).toBe('');
    expect((await render({ ...OUTAGE, since: OUTAGE.since + 99999 })).textContent).toContain('unreachable for');
  });
});
