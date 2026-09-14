// @vitest-environment jsdom
//
// #546: the Rules page must SAY when a saved probe root does not exist. The
// check is computed server-side and carried on the schedule draft; this drives
// the real ProbeRootsEditor through the vendored-React harness (#517) so the
// warning is proven rendered, not only computed.
import { describe, it, expect, beforeAll, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

let ProbeRootsEditor;

function loadVendoredReact() {
  for (const name of ['react.production.min.js', 'react-dom.production.min.js']) {
    const url = path.resolve(process.cwd(), 'src/subarr/static/v1/vendor', name);
    (0, eval)(fs.readFileSync(url, 'utf8'));
  }
}

beforeAll(async () => {
  loadVendoredReact();
  ({ ProbeRootsEditor } = await import('../rules.jsx'));
});

const roots = [];
afterEach(() => {
  for (const r of roots.splice(0)) r.unmount();
  document.body.innerHTML = '';
});

async function render(props) {
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = ReactDOM.createRoot(host);
  roots.push(root);
  root.render(React.createElement(ProbeRootsEditor, { onChange: () => {}, ...props }));
  await new Promise((r) => setTimeout(r, 20));
  return host;
}

describe('ProbeRootsEditor', () => {
  it('shows a warning naming the saved root that was not found', async () => {
    const host = await render({
      value: ['Film', 'TV'],
      check: [
        { root: 'Film', ok: true, reason: null },
        { root: 'TV', ok: false, reason: 'root not found: TV' },
      ],
    });
    const alert = host.querySelector('[role="alert"]');
    expect(alert).not.toBeNull();
    expect(alert.textContent).toMatch(/TV/);
    expect(alert.textContent).not.toMatch(/Film/);
  });

  it('does not warn about a root the user already removed from the draft', async () => {
    const host = await render({
      value: ['Film'],
      check: [{ root: 'TV', ok: false, reason: 'root not found: TV' }],
    });
    expect(host.querySelector('[role="alert"]')).toBeNull();
  });

  it('shows no warning when every root resolves or there is no check', async () => {
    const ok = await render({ value: ['Film'], check: [{ root: 'Film', ok: true, reason: null }] });
    expect(ok.querySelector('[role="alert"]')).toBeNull();
    const none = await render({ value: ['Film'] });
    expect(none.querySelector('[role="alert"]')).toBeNull();
  });
});
