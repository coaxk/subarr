// @vitest-environment jsdom
//
// #633: the Settings card that turns on per-file transcribe/translate. It must
// render the server's availability (disabled, with the reason shown, when the
// connected subgen cannot do it) and POST what the user typed, split on commas
// and whitespace.
//
// React is vendored (static/v1/vendor), not an npm dependency.
import { describe, it, expect, beforeAll, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

let PerFileTaskCard;
let derivePerFileTaskView;
let splitWantedLanguages;
let calls;
let getBody;
let postResponse;

function loadVendoredReact() {
  for (const name of ['react.production.min.js', 'react-dom.production.min.js']) {
    const url = path.resolve(process.cwd(), 'src/subarr/static/v1/vendor', name);
    (0, eval)(fs.readFileSync(url, 'utf8'));
  }
}

beforeAll(async () => {
  loadVendoredReact();
  ({ PerFileTaskCard, derivePerFileTaskView, splitWantedLanguages } = await import('../settings.jsx'));
});

beforeEach(() => {
  calls = [];
  getBody = { enabled: false, wanted: [], available: true, unavailable_reason: null,
    preferred_audio_languages: ['en'], force_detected_language_to: null };
  postResponse = null;
  const stub = (url, init) => {
    calls.push({ url: String(url), init });
    if (init && init.method === 'POST') {
      const body = postResponse
        ? postResponse
        : { ...getBody, ...JSON.parse(init.body) };
      const ok = !(body && body.status === 400);
      return Promise.resolve({ ok, status: ok ? 200 : 400, json: async () => (ok ? body : { detail: body.detail }) });
    }
    return Promise.resolve({ ok: true, status: 200, json: async () => getBody });
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

async function render() {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container);
  roots.push(root);
  root.render(React.createElement(PerFileTaskCard));
  await settle();
  return container;
}

async function click(el) {
  el.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  await settle();
}

function setValue(input, value) {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  setter.call(input, value);
  input.dispatchEvent(new window.Event('input', { bubbles: true }));
}

const saveBtn = (c) => [...c.querySelectorAll('button')].find((b) => (b.textContent || '').trim() === 'Save');
const toggle = (c) => c.querySelector('[role="switch"]');
const field = (c) => c.querySelector('input[type="text"]');

describe('splitWantedLanguages', () => {
  it('splits on commas and whitespace and drops empties', () => {
    expect(splitWantedLanguages('en, es  ja,,')).toEqual(['en', 'es', 'ja']);
    expect(splitWantedLanguages('')).toEqual([]);
    expect(splitWantedLanguages(null)).toEqual([]);
  });
});

describe('derivePerFileTaskView', () => {
  it('null status is unavailable with a reason', () => {
    const v = derivePerFileTaskView(null);
    expect(v.disabled).toBe(true);
    expect(v.reason).toBeTruthy();
  });
});

describe('PerFileTaskCard (#633)', () => {
  it('available: controls are enabled, no reason is shown, and the explanation is there', async () => {
    getBody = { ...getBody, enabled: true, wanted: ['en', 'es'] };
    const c = await render();
    expect(calls[0].url).toContain('/api/per-file-task/config');
    expect(c.textContent).toContain('Translate audio that is not a wanted language into English');
    expect(c.textContent).toContain('Wanted subtitle languages (e.g. en, es)');
    expect(c.textContent).toContain('Uses the same audio track subgen will pick.');
    expect(toggle(c).disabled).toBe(false);
    expect(toggle(c).getAttribute('aria-checked')).toBe('true');
    expect(field(c).disabled).toBe(false);
    expect(field(c).value).toBe('en, es');
    expect(c.querySelector('[role="note"]')).toBeNull();
  });

  it('unavailable: controls are disabled and the reason is shown', async () => {
    getBody = { ...getBody, available: false,
      unavailable_reason: 'subgen does not advertise its preferred audio languages; update subarr-subgen to v4.38+' };
    const c = await render();
    expect(toggle(c).disabled).toBe(true);
    expect(field(c).disabled).toBe(true);
    expect(saveBtn(c).disabled).toBe(true);
    expect(c.querySelector('[role="note"]').textContent).toContain('update subarr-subgen to v4.38+');
  });

  it('save: POSTs the toggle and the field split on commas and whitespace', async () => {
    const c = await render();
    await click(toggle(c));
    setValue(field(c), 'en, es  ja');
    await settle();
    await click(saveBtn(c));
    const post = calls.find((x) => x.init && x.init.method === 'POST');
    expect(post.url).toContain('/api/per-file-task/config');
    expect(JSON.parse(post.init.body)).toEqual({ enabled: true, wanted: ['en', 'es', 'ja'] });
    expect(c.textContent).toContain('Saved');
  });

  it('save: shows the server message on a 400 and does not claim it saved', async () => {
    const c = await render();
    await click(toggle(c));
    postResponse = { status: 400, detail: 'enter at least one wanted subtitle language to turn this on' };
    await click(saveBtn(c));
    expect(c.querySelector('[role="alert"]').textContent).toContain('at least one wanted subtitle language');
    expect(c.textContent).not.toContain('Saved');
  });

  it('a failed load shows the error instead of the controls', async () => {
    globalThis.fetch = window.fetch = () => Promise.resolve({ ok: false, status: 500, json: async () => ({}) });
    const c = await render();
    expect(c.textContent).toContain('Could not query');
    expect(toggle(c)).toBeNull();
  });
});
