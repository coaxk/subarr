// @vitest-environment jsdom
//
// #546: the First walk step used to pre-fill `TV, Movies` whether or not those
// folders existed (#524: a library of `Film` and `Serie Tv` then failed every
// scheduled walk). It now asks the server which folders exist. Drives the real
// StepWalk through the vendored-React harness (#517) so the suggestion is proven
// rendered, not only fetched.
//
// Effects are awaited by POLLING, like the other harness tests: a fixed sleep
// cannot tell "never fetched" from "not yet", and read as a false failure here.
import { describe, it, expect, beforeAll, beforeEach, afterEach, vi } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

let StepWalk;

function loadVendoredReact() {
  for (const name of ['react.production.min.js', 'react-dom.production.min.js']) {
    const url = path.resolve(process.cwd(), 'src/subarr/static/v1/vendor', name);
    (0, eval)(fs.readFileSync(url, 'utf8'));
  }
}

beforeAll(async () => {
  loadVendoredReact();
  ({ StepWalk } = await import('../onboarding.jsx'));
});

let fetchCalls;
let resolveSuggestions;

beforeEach(() => {
  fetchCalls = [];
  resolveSuggestions = null;
  globalThis.fetch = vi.fn((url) => {
    fetchCalls.push(String(url));
    if (String(url).includes('/api/onboarding/probe-root-suggestions')) {
      return new Promise((resolve) => {
        resolveSuggestions = (roots) => resolve({ ok: true, json: async () => ({ roots }) });
      });
    }
    return Promise.resolve({ ok: false, json: async () => ({}) });
  });
});

const roots = [];
afterEach(() => {
  for (const r of roots.splice(0)) r.unmount();
  document.body.innerHTML = '';
});

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function until(pred, { timeout = 3000, every = 10, what = 'condition' } = {}) {
  const t0 = Date.now();
  for (;;) {
    const v = pred();
    if (v) return v;
    if (Date.now() - t0 > timeout) throw new Error(`timed out waiting for ${what}`);
    await sleep(every);
  }
}

function render(progress) {
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = ReactDOM.createRoot(host);
  roots.push(root);
  root.render(
    React.createElement(StepWalk, {
      progress,
      setField: () => {},
      walkResult: null,
      onStart: () => {},
      isStarting: false,
    }),
  );
  return host;
}

function rootsInput(host) {
  return [...host.querySelectorAll('input')].find((i) => /library root/i.test(i.getAttribute('placeholder') || ''));
}

const askedForSuggestions = () => fetchCalls.some((u) => u.includes('probe-root-suggestions'));

describe('StepWalk probe roots', () => {
  it('fills in the folders that exist when nothing was chosen, never TV, Movies', async () => {
    const host = render({ media_root: '/data/Multimedia' });
    await until(() => rootsInput(host), { what: 'the probe roots input' });
    expect(rootsInput(host).value).toBe('');

    await until(askedForSuggestions, { what: 'the suggestions request' });
    resolveSuggestions(['Film', 'Serie Tv']);

    await until(() => rootsInput(host).value === 'Film, Serie Tv', { what: 'the suggested roots in the input' });
    expect(host.textContent).not.toMatch(/TV, Movies/);
  });

  it('keeps roots already chosen and does not ask for suggestions', async () => {
    const host = render({ probe_roots: ['Anime'] });
    await until(() => rootsInput(host), { what: 'the probe roots input' });
    expect(rootsInput(host).value).toBe('Anime');
    await sleep(100); // long enough for the mount effect to have run
    expect(askedForSuggestions()).toBe(false);
  });

  it('does not overwrite what the user typed before the suggestions arrived', async () => {
    const host = render({});
    const input = await until(() => rootsInput(host), { what: 'the probe roots input' });
    await until(askedForSuggestions, { what: 'the suggestions request' });

    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(input, 'Kids');
    input.dispatchEvent(new Event('input', { bubbles: true }));
    await until(() => rootsInput(host).value === 'Kids', { what: 'the typed text' });

    resolveSuggestions(['Film', 'Serie Tv']);
    await sleep(100);
    expect(rootsInput(host).value).toBe('Kids');
  });
});
