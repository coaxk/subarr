// @vitest-environment jsdom
//
// #568: Review offers a series rule when several of a show's episode verdicts
// agree. Component-level, for the reason #517 gives: the helper can be perfect
// while the card that renders it is unwired, and only the seam catches that.
//
// React is vendored (static/v1/vendor), not an npm dependency — load THAT file,
// so the tested React is the shipped one.
import { describe, it, expect, beforeAll, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

let SeriesSuggestionCard;
let calls;

const SUGGESTION = {
  series_prefix: 'TV/Alerts/',
  title: 'Alerts',
  lang_code: 'fr',
  agreeing: 6,
  language_name: 'French',
  sample_paths: ['TV/Alerts/Season 1/Alerts - S01E01.mkv'],
};

function loadVendoredReact() {
  for (const name of ['react.production.min.js', 'react-dom.production.min.js']) {
    const url = path.resolve(process.cwd(), 'src/subarr/static/v1/vendor', name);
    (0, eval)(fs.readFileSync(url, 'utf8'));
  }
}

beforeAll(async () => {
  loadVendoredReact();
  ({ SeriesSuggestionCard } = await import('../review.jsx'));
});

let items;
beforeEach(() => {
  calls = [];
  items = [SUGGESTION];
  const stub = (url, opts) => {
    calls.push({ url: String(url), method: (opts && opts.method) || 'GET', body: opts && opts.body });
    if (String(url).includes('/series-suggestions/apply')) {
      items = [];
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ applied: true }) });
    }
    if (String(url).includes('/series-suggestions/dismiss')) {
      items = [];
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ dismissed: true }) });
    }
    return Promise.resolve({ ok: true, status: 200, json: async () => ({ items }) });
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

async function renderCard() {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container);
  roots.push(root);
  root.render(React.createElement(SeriesSuggestionCard));
  await settle();
  return container;
}

function button(container, label) {
  return [...container.querySelectorAll('button')].find((b) =>
    (b.textContent || '').toLowerCase().includes(label));
}

async function click(el) {
  el.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  await settle();
}

describe('SeriesSuggestionCard (#568)', () => {
  it('asks the server what to suggest', async () => {
    await renderCard();
    expect(calls.some((c) => c.url.includes('/api/audio-lang/series-suggestions'))).toBe(true);
  });

  it('names the show, the language and how many episodes agree', async () => {
    const c = await renderCard();
    const text = c.textContent;
    expect(text).toContain('Alerts');
    expect(text).toContain('6');
    expect(text.toLowerCase()).toContain('french');
  });

  it('applies the rule with the language that was offered', async () => {
    const c = await renderCard();
    await click(button(c, 'apply'));
    const apply = calls.find((x) => x.url.includes('/series-suggestions/apply'));
    expect(apply, `no apply call; calls: ${calls.map((x) => x.url)}`).toBeTruthy();
    expect(apply.method).toBe('POST');
    expect(JSON.parse(apply.body)).toEqual({ series_prefix: 'TV/Alerts/', lang_code: 'fr' });
  });

  it('re-reads the list after applying instead of trusting the POST', async () => {
    const c = await renderCard();
    const before = calls.filter((x) => x.method === 'GET').length;
    await click(button(c, 'apply'));
    expect(calls.filter((x) => x.method === 'GET').length).toBeGreaterThan(before);
    expect(c.textContent).not.toContain('Alerts');
  });

  it('dismisses one show without applying anything', async () => {
    const c = await renderCard();
    await click(button(c, 'not this show'));
    const dismiss = calls.find((x) => x.url.includes('/series-suggestions/dismiss'));
    expect(dismiss.method).toBe('POST');
    expect(JSON.parse(dismiss.body)).toEqual({ series_prefix: 'TV/Alerts/' });
    expect(calls.some((x) => x.url.includes('/apply'))).toBe(false);
  });

  it('renders nothing at all when there is nothing to suggest', async () => {
    items = [];
    const c = await renderCard();
    expect(c.textContent.trim()).toBe('');
  });

  it('stays silent when the endpoint fails', async () => {
    const stub = () => Promise.resolve({ ok: false, status: 500, json: async () => ({}) });
    globalThis.fetch = stub;
    window.fetch = stub;
    const c = await renderCard();
    expect(c.textContent.trim()).toBe('');
  });
});

// The seam #517 was written about: the card can be perfect and never mounted.
describe('ReviewPage mounts the card (#568)', () => {
  it('asks for suggestions when the page renders', async () => {
    const { ReviewPage } = await import('../review.jsx');
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = ReactDOM.createRoot(container);
    roots.push(root);
    root.render(React.createElement(ReviewPage));
    await settle();
    expect(
      calls.some((c) => c.url.includes('/api/audio-lang/series-suggestions')),
      `Review never requested series suggestions; calls: ${calls.map((c) => c.url)}`,
    ).toBe(true);
  });
});
