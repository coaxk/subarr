// @vitest-environment jsdom
//
// #549: the probe-roots field is free text, so a root has to be typed from
// memory — and #524 shows what that costs when the guess is wrong. The picker
// offers what exists and writes the canonical form the field already accepts.
//
// React is vendored (static/v1/vendor), not an npm dependency.
import { describe, it, expect, beforeAll, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

let ProbeRootsEditor;
let calls;

const LIBRARIES = [
  { slug: '', name: 'default', library_root: '', roots: ['Film', 'Serie Tv'] },
  { slug: 'disk2', name: 'Disk 2', library_root: '@disk2', roots: ['@disk2/Anime'] },
];

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

beforeEach(() => {
  calls = [];
  const stub = (url) => {
    calls.push(String(url));
    return Promise.resolve({ ok: true, status: 200, json: async () => ({ libraries: LIBRARIES }) });
  };
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

async function render(value = [], onChange = () => {}) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container);
  roots.push(root);
  root.render(React.createElement(ProbeRootsEditor, { value, onChange, check: [] }));
  await settle();
  return container;
}

function byText(container, text) {
  return [...container.querySelectorAll('button')].find(
    (b) => (b.textContent || '').trim() === text);
}

async function click(el) {
  el.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  await settle();
}

describe('ProbeRootsEditor picker (#549)', () => {
  it('asks the server which roots exist', async () => {
    await render();
    expect(calls.some((u) => u.includes('/api/probe-roots/suggestions'))).toBe(true);
  });

  it('offers the folders of the default library', async () => {
    const c = await render();
    await click(byText(c, 'Pick'));
    expect(byText(c, 'Film')).toBeTruthy();
    expect(byText(c, 'Serie Tv')).toBeTruthy();
  });

  it('writes a plain folder name for the default library', async () => {
    let written = null;
    const c = await render([], (v) => { written = v; });
    await click(byText(c, 'Pick'));
    await click(byText(c, 'Film'));
    expect(written).toEqual(['Film']);
  });

  it('writes the slug-qualified form for a second library', async () => {
    let written = null;
    const c = await render([], (v) => { written = v; });
    await click(byText(c, 'Pick'));
    await click(byText(c, '@disk2/Anime'));
    expect(written).toEqual(['@disk2/Anime']);
  });

  it('adds to what is already there rather than replacing it', async () => {
    let written = null;
    const c = await render(['TV'], (v) => { written = v; });
    await click(byText(c, 'Pick'));
    await click(byText(c, 'Film'));
    expect(written).toEqual(['TV', 'Film']);
  });

  it('never writes the same root twice', async () => {
    let written = 'untouched';
    const c = await render(['Film'], (v) => { written = v; });
    await click(byText(c, 'Pick'));
    await click(byText(c, 'Film'));
    expect(written).toBe('untouched');
  });

  it('still allows typing, which is the escape hatch', async () => {
    const c = await render();
    expect(c.querySelector('input[type="text"]')).toBeTruthy();
  });

  it('stays usable when the endpoint fails', async () => {
    const stub = () => Promise.resolve({ ok: false, status: 500, json: async () => ({}) });
    globalThis.fetch = stub;
    window.fetch = stub;
    const c = await render();
    expect(c.querySelector('input[type="text"]')).toBeTruthy();
  });
});
