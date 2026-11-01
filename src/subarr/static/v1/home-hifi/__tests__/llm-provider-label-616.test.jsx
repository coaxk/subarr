// @vitest-environment jsdom
//
// #616: an OpenAI-compatible LLM install is labelled "OpenAI-compatible"
// wherever the integration name is shown; Ollama installs are unchanged.
// React is vendored (static/v1/vendor), not an npm dependency.
import { describe, it, expect, beforeAll, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

let buildRailItems;
let integrationTitle;
let IntegrationTile;

function loadVendoredReact() {
  for (const name of ['react.production.min.js', 'react-dom.production.min.js']) {
    const url = path.resolve(process.cwd(), 'src/subarr/static/v1/vendor', name);
    (0, eval)(fs.readFileSync(url, 'utf8'));
  }
}

beforeAll(async () => {
  loadVendoredReact();
  ({ buildRailItems } = await import('../settings.jsx'));
  ({ integrationTitle, IntegrationTile } = await import('../dashboard.jsx'));
});

const roots = [];
afterEach(() => {
  for (const r of roots.splice(0)) r.unmount();
  document.body.innerHTML = '';
});

async function settle() {
  for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0));
}

async function render(element) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container);
  roots.push(root);
  root.render(element);
  await settle();
  return container;
}

function itemFor(items, id) {
  return items.find((x) => x.id === id);
}

function health(integrations) {
  return { integrations, subgen: { reachable: true } };
}

describe('buildRailItems (#616)', () => {
  it('openai provider labels the LLM item OpenAI-compatible', () => {
    const items = buildRailItems(health([
      { name: 'ollama', online: true, configured: true, provider: 'openai' },
    ]));
    const item = itemFor(items, 'ollama');
    expect(item.name).toBe('OpenAI-compatible');
    expect(item.status).toBe('ok');
  });

  it('ollama provider keeps the Ollama label', () => {
    const items = buildRailItems(health([
      { name: 'ollama', online: true, configured: true, provider: 'ollama' },
    ]));
    expect(itemFor(items, 'ollama').name).toBe('Ollama');
  });

  it('missing provider keeps the Ollama label', () => {
    const items = buildRailItems(health([
      { name: 'ollama', online: true, configured: true },
    ]));
    expect(itemFor(items, 'ollama').name).toBe('Ollama');
  });

  it('provider is trimmed and case-folded', () => {
    const items = buildRailItems(health([
      { name: 'ollama', online: true, configured: true, provider: ' OpenAI ' },
    ]));
    expect(itemFor(items, 'ollama').name).toBe('OpenAI-compatible');
  });

  it('unknown provider keeps the Ollama label', () => {
    const bogus = buildRailItems(health([
      { name: 'ollama', online: true, configured: true, provider: 'bogus' },
    ]));
    expect(itemFor(bogus, 'ollama').name).toBe('Ollama');
    const nul = buildRailItems(health([
      { name: 'ollama', online: true, configured: true, provider: null },
    ]));
    expect(itemFor(nul, 'ollama').name).toBe('Ollama');
  });

  it('other integrations ignore a provider field', () => {
    const items = buildRailItems(health([
      { name: 'bazarr', online: true, configured: true, provider: 'openai' },
      { name: 'sonarr', online: false, configured: true },
      { name: 'ollama', online: true, configured: true, provider: 'openai' },
    ]));
    expect(itemFor(items, 'bazarr').name).toBe('Bazarr');
    expect(itemFor(items, 'sonarr').name).toBe('Sonarr');
    expect(itemFor(items, 'subgen').name).toBe('Subgen');
  });

  it('rail order and ids are unchanged', () => {
    const items = buildRailItems(health([
      { name: 'bazarr', online: true, configured: true },
      { name: 'sonarr', online: false, configured: true },
      { name: 'ollama', online: true, configured: true, provider: 'openai' },
    ]));
    expect(items.map((x) => x.id)).toEqual(['bazarr', 'sonarr', 'subgen', 'ollama']);
  });

  it('no health gives no items', () => {
    expect(buildRailItems(null)).toEqual([]);
  });
});

describe('integrationTitle (#616)', () => {
  it('title table', () => {
    const table = [
      [{ name: 'ollama', provider: 'openai' }, 'OpenAI-compatible'],
      [{ name: 'ollama', provider: 'ollama' }, 'Ollama'],
      [{ name: 'ollama' }, 'Ollama'],
      [{ name: 'ollama', provider: ' OPENAI ' }, 'OpenAI-compatible'],
      [{ name: 'bazarr', provider: 'openai' }, 'Bazarr'],
      [{ name: 'plex' }, 'Plex'],
    ];
    for (const [i, expected] of table) {
      expect(integrationTitle(i), JSON.stringify(i)).toBe(expected);
    }
  });
});

describe('IntegrationTile (#616)', () => {
  it('tile shows OpenAI-compatible for the openai provider', async () => {
    const i = { name: 'ollama', provider: 'openai', status: 'ok', version: '', ping: 0,
      extra: '2 models · m1', configured: true };
    const c = await render(React.createElement(IntegrationTile, { i }));
    expect([...c.querySelectorAll('*')].some((el) => el.textContent.trim() === 'OpenAI-compatible')).toBe(true);
    expect(c.textContent).not.toContain('Ollama');
    expect(c.querySelector('[title="OpenAI-compatible version reported by /system/status"]')).not.toBeNull();
    expect(c.querySelector('span').textContent.trim()).toBe('O');
  });

  it('tile keeps Ollama for the ollama provider', async () => {
    const i = { name: 'ollama', provider: 'ollama', status: 'ok', version: '', ping: 0,
      extra: '2 models · m1', configured: true };
    const c = await render(React.createElement(IntegrationTile, { i }));
    expect([...c.querySelectorAll('*')].some((el) => el.textContent.trim() === 'Ollama')).toBe(true);
    expect(c.textContent).not.toContain('OpenAI-compatible');
    expect(c.querySelector('span').textContent.trim()).toBe('🦙');
  });
});
