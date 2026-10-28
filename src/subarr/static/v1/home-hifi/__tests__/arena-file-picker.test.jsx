// @vitest-environment jsdom
//
// Tuning Lab's "Pick a media file" dialog reused Library's browse helper but
// always opened the empty path, which is only the default library. A second
// library is `@slug/` and has to skip rollup at that root, the same way
// Library → Browse already does.
//
// React is vendored (static/v1/vendor), not an npm dependency.
import { describe, it, expect, beforeAll, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

let FilePicker;
let pickerBrowseTarget;
let pickerStartPath;
let browseQuery;
let browseCache;
let calls;
let picked;

const ONE = [{ slug: '', name: 'Movies', is_default: true }];
const TWO = [
  { slug: '', name: 'Movies', is_default: true },
  { slug: 'tv', name: 'TV', is_default: false },
];

const TREES = {
  '': {
    entries: [
      { name: 'Film', path: 'Film', is_dir: true },
      { name: 'clip.mkv', path: 'Film/clip.mkv', is_dir: false },
    ],
  },
  '@tv/': {
    entries: [{ name: 'Show', path: '@tv/Show', is_dir: true }],
  },
  '@tv/Show': {
    entries: [{ name: 'ep.mkv', path: '@tv/Show/ep.mkv', is_dir: false }],
  },
};

function loadVendoredReact() {
  for (const name of ['react.production.min.js', 'react-dom.production.min.js']) {
    const url = path.resolve(process.cwd(), 'src/subarr/static/v1/vendor', name);
    (0, eval)(fs.readFileSync(url, 'utf8'));
  }
}

beforeAll(async () => {
  loadVendoredReact();
  ({ FilePicker, pickerBrowseTarget, pickerStartPath } = await import('../arena.jsx'));
  ({ browseQuery, browseCache } = await import('../library.jsx'));
});

beforeEach(() => {
  calls = [];
  picked = [];
  browseCache.clear();
  const stub = (url) => {
    const u = String(url);
    calls.push(u);
    if (u.includes('/api/settings/libraries')) {
      const libraries = globalThis.__arenaPickerLibraries || TWO;
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ libraries }) });
    }
    const parsed = new URL(u, 'http://picker.test');
    const key = parsed.searchParams.get('path') || '';
    const body = TREES[key] || { entries: [] };
    return Promise.resolve({ ok: true, status: 200, json: async () => body });
  };
  globalThis.fetch = stub;
  window.fetch = stub;
});

const roots = [];
afterEach(() => {
  for (const r of roots.splice(0)) r.unmount();
  document.body.innerHTML = '';
  delete globalThis.__arenaPickerLibraries;
});

async function settle() {
  for (let i = 0; i < 12; i++) await new Promise((r) => setTimeout(r, 0));
}

async function render(props = {}) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container);
  roots.push(root);
  root.render(React.createElement(FilePicker, {
    onPick: (p) => picked.push(p),
    onPickMany: () => {},
    onClose: () => {},
    ...props,
  }));
  await settle();
  return container;
}

function browseCalls() {
  return calls.filter((u) => u.includes('/api/browse')).map((u) => {
    const url = new URL(u, 'http://picker.test');
    return { path: url.searchParams.get('path') || '', rollup: url.searchParams.get('rollup') };
  });
}

function buttonIncluding(container, text) {
  return [...container.querySelectorAll('button')].find((b) => (b.textContent || '').includes(text));
}

async function click(el) {
  el.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  await settle();
}

async function chooseLibrary(container, slug) {
  const select = container.querySelector('select[aria-label="Library"]');
  const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
  setter.call(select, slug);
  select.dispatchEvent(new window.Event('change', { bubbles: true }));
  await settle();
}

describe('picker browse target', () => {
  it('treats the empty path as the default library root and skips rollup', () => {
    const t = pickerBrowseTarget('');
    expect(t).toMatchObject({ slug: '', relative: '', atRoot: true, browsePath: '' });
    expect(browseQuery(t.browsePath, t.atRoot)).toBe('?rollup=false');
  });

  it('roots a second library at @slug/ and skips rollup there', () => {
    const t = pickerBrowseTarget('@tv/');
    expect(t).toMatchObject({ slug: 'tv', relative: '', atRoot: true, browsePath: '@tv/' });
    expect(browseQuery(t.browsePath, t.atRoot)).toBe('?path=%40tv%2F&rollup=false');
  });

  it('keeps the @slug head while drilling in, with rollup on', () => {
    const t = pickerBrowseTarget('@tv/Show/Season 1');
    expect(t).toMatchObject({ slug: 'tv', relative: 'Show/Season 1', atRoot: false, browsePath: '@tv/Show/Season 1' });
    expect(browseQuery(t.browsePath, t.atRoot)).toBe('?path=%40tv%2FShow%2FSeason%201');
  });

  it('leaves a default-library path unqualified', () => {
    const t = pickerBrowseTarget('Film/clip.mkv');
    expect(t).toMatchObject({ slug: '', atRoot: false, browsePath: 'Film/clip.mkv' });
    expect(browseQuery(t.browsePath, t.atRoot)).toBe('?path=Film%2Fclip.mkv');
  });

  it('opens on the library that owns the path already in the media field', () => {
    expect(pickerStartPath('@tv/Show/ep.mkv')).toBe('@tv/');
    expect(pickerStartPath('Film/clip.mkv')).toBe('');
    expect(pickerStartPath('')).toBe('');
  });
});

describe('FilePicker library switcher', () => {
  it('hides the switcher and browses the default root when only one library is configured', async () => {
    globalThis.__arenaPickerLibraries = ONE;
    const c = await render();
    expect(c.querySelector('select[aria-label="Library"]')).toBeNull();
    expect(browseCalls()[0]).toEqual({ path: '', rollup: 'false' });
    expect(buttonIncluding(c, 'library').textContent).toBe('library');
    expect(buttonIncluding(c, 'Film')).toBeTruthy();
  });

  it('browses @slug/ with rollup off when another library is chosen, and picks that canonical path', async () => {
    const c = await render();
    const select = c.querySelector('select[aria-label="Library"]');
    expect([...select.options].map((o) => o.textContent)).toEqual(['Movies', 'TV']);
    expect(browseCalls()[0]).toEqual({ path: '', rollup: 'false' });

    await chooseLibrary(c, 'tv');
    expect(browseCalls().some((b) => b.path === '@tv/' && b.rollup === 'false')).toBe(true);
    expect(buttonIncluding(c, 'Show')).toBeTruthy();

    await click(buttonIncluding(c, 'Show'));
    const drilled = browseCalls().find((b) => b.path === '@tv/Show');
    expect(drilled).toEqual({ path: '@tv/Show', rollup: null });

    await click(buttonIncluding(c, 'ep.mkv'));
    expect(picked).toEqual(['@tv/Show/ep.mkv']);
  });

  it('returns the root crumb to the current library, not the default one', async () => {
    const c = await render({ initialPath: '@tv/Show/ep.mkv' });
    expect(browseCalls()[0]).toEqual({ path: '@tv/', rollup: 'false' });
    await click(buttonIncluding(c, 'Show'));
    expect(buttonIncluding(c, 'ep.mkv')).toBeTruthy();

    await click(buttonIncluding(c, 'TV'));
    expect(buttonIncluding(c, 'Show')).toBeTruthy();
    expect(buttonIncluding(c, 'ep.mkv')).toBeFalsy();
    expect(browseCalls().some((b) => b.path === '' )).toBe(false);
  });

  it('keeps ticked files when the library changes', async () => {
    const c = await render();
    const box = c.querySelector('input[type="checkbox"]');
    // React 18 fires a checkbox's onChange from the click, which is also what a user does.
    box.click();
    await settle();
    expect(c.textContent).toContain('1 file selected for bulk sweep');

    await chooseLibrary(c, 'tv');
    expect(c.textContent).toContain('1 file selected for bulk sweep');
    expect(buttonIncluding(c, 'Show')).toBeTruthy();
  });
});
