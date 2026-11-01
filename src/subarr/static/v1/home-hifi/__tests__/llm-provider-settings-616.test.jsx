// @vitest-environment jsdom
//
// #616: the LLM settings panel — provider picker, the OpenAI-compatible
// credential editor, the per-step test rows and the SystemPanel unload row.
// React is vendored (static/v1/vendor), not an npm dependency.
import { describe, it, expect, beforeAll, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

let resolveProfile;
let LlmSection;
let TestSteps;
let SystemPanel;

function loadVendoredReact() {
  for (const name of ['react.production.min.js', 'react-dom.production.min.js']) {
    const url = path.resolve(process.cwd(), 'src/subarr/static/v1/vendor', name);
    (0, eval)(fs.readFileSync(url, 'utf8'));
  }
}

beforeAll(async () => {
  loadVendoredReact();
  ({ resolveProfile, LlmSection, TestSteps, SystemPanel } = await import('../settings.jsx'));
});

let handlers;
let calls;
let llmProvider;
let openaiValues;
let refetchCalls;

function ok(body, status = 200) {
  return { ok: true, status, json: async () => body };
}

function fail(status, detail) {
  return { ok: false, status, json: async () => ({ detail }) };
}

function on(method, url, reply) {
  handlers.push({ method, url, reply });
}

function field(value, { secret = false, envManaged = false, hasValue = true } = {}) {
  return { env_managed: envManaged, has_value: hasValue, value, secret };
}

function llmConfig({ envManaged = false, status = 200 } = {}) {
  on('GET', '/api/integrations/llm/config', () => (
    status === 200
      ? ok({ name: 'llm', fields: { provider: field(llmProvider, { envManaged }) } })
      : fail(status, 'boom')
  ));
}

function ollamaConfig() {
  on('GET', '/api/integrations/ollama/config', () => ok({
    name: 'ollama',
    fields: { url: field('http://ollama:11434'), model: field('qwen2.5') },
  }));
}

function openaiConfig({ envManaged = {} } = {}) {
  on('GET', '/api/integrations/openai/config', () => ok({
    name: 'openai',
    fields: {
      url: field(openaiValues.url, { envManaged: !!envManaged.url }),
      api_key: { env_managed: !!envManaged.api_key, has_value: true, value: openaiValues.api_key, secret: true },
      model: field(openaiValues.model, { envManaged: !!envManaged.model }),
      vision: field(openaiValues.vision, { envManaged: !!envManaged.vision }),
      server_type: field(openaiValues.server_type, { envManaged: !!envManaged.server_type }),
    },
  }));
}

function visionStatus() {
  on('GET', '/api/vision/status', () => ok({ ollama_configured: false, provider: 'ollama' }));
}

function baseLlm({ envManaged = false, llmStatus = 200 } = {}) {
  llmConfig({ envManaged, status: llmStatus });
  ollamaConfig();
  openaiConfig();
  visionStatus();
}

beforeEach(() => {
  handlers = [];
  calls = [];
  llmProvider = 'ollama';
  openaiValues = { url: 'http://unsloth:8000/v1', api_key: '••••abcd', model: 'm', vision: 'off', server_type: 'auto' };
  refetchCalls = [];
  const stub = (url, init) => {
    const u = String(url);
    const method = String((init && init.method) || 'GET').toUpperCase();
    calls.push({ url: u, method, init: init || {} });
    for (const h of handlers) {
      if (h.method === method && h.url === u) {
        return Promise.resolve(typeof h.reply === 'function' ? h.reply({ url: u, init }) : h.reply);
      }
    }
    return Promise.resolve({ ok: false, status: 404, json: async () => ({ detail: 'not found' }) });
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
  for (let i = 0; i < 12; i++) await new Promise((r) => setTimeout(r, 0));
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

async function click(el) {
  el.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  await settle();
}

function setValue(input, value) {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  setter.call(input, value);
  input.dispatchEvent(new window.Event('input', { bubbles: true }));
}

async function setSelect(select, value) {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
  setter.call(select, value);
  select.dispatchEvent(new window.Event('change', { bubbles: true }));
  await settle();
}

function labelEl(container, text) {
  return [...container.querySelectorAll('label')].find((l) => {
    const first = l.querySelector('span');
    return !!first && first.textContent.trim() === text;
  }) || null;
}

function selectIn(container, label) {
  const l = labelEl(container, label);
  return l ? l.querySelector('select') : null;
}

function inputIn(container, label) {
  const l = labelEl(container, label);
  return l ? l.querySelector('input') : null;
}

function buttonByText(container, text) {
  return [...container.querySelectorAll('button')].find((b) => (b.textContent || '').trim() === text) || null;
}

const refetchHealth = (arg) => { refetchCalls.push(arg); };
const renderLlm = () => render(React.createElement(LlmSection, { refetchHealth }));

const SENT = 'Sent to this service: titles with library name and file path inside the library, Arena score summaries with the file name, and video frames when vision is on.';

describe('resolveProfile (#616)', () => {
  it('resolves the documented profile table', () => {
    const table = [
      ['http://unsloth:8000/v1', 'auto', 'local'],
      ['https://api.openai.com/v1', 'auto', 'openai'],
      ['https://openrouter.ai/api/v1', 'auto', 'openrouter'],
      ['https://eu.openrouter.ai/api/v1', 'auto', 'openrouter'],
      ['https://notopenrouter.ai/v1', 'auto', 'local'],
      ['https://api.openai.com.evil.example/v1', 'auto', 'local'],
      ['https://api.openai.com/v1', 'local', 'local'],
      ['http://gw:8080/v1', ' OpenRouter ', 'openrouter'],
      ['http://gw:8080/v1', 'bogus', 'local'],
      ['https://api.openai.com/v1', null, 'openai'],
      ['', 'auto', 'local'],
      ['not a url', 'auto', 'local'],
      ['  https://api.openai.com/v1  ', 'auto', 'openai'],
    ];
    for (const [url, serverType, expected] of table) {
      expect(resolveProfile(url, serverType), `${url} / ${serverType}`).toBe(expected);
    }
  });
});

describe('LlmSection (#616)', () => {
  it('renders both provider options', async () => {
    baseLlm();
    const c = await renderLlm();
    const select = selectIn(c, 'Provider');
    expect([...select.options].map((o) => o.value)).toEqual(['ollama', 'openai']);
    expect([...select.options].map((o) => o.textContent)).toEqual(['Ollama', 'OpenAI-compatible']);
  });

  it('saved ollama shows the Ollama editor and the vision card', async () => {
    baseLlm();
    const c = await renderLlm();
    expect(labelEl(c, 'URL')).not.toBeNull();
    expect(labelEl(c, 'Model')).not.toBeNull();
    expect(c.textContent).toContain('Vision pre-filter');
    expect(c.querySelectorAll('input[type="password"]')).toHaveLength(0);
    expect(c.textContent).not.toContain('Provider change not saved.');
  });

  it('choosing OpenAI-compatible shows its fields and hides the Ollama card', async () => {
    baseLlm();
    const c = await renderLlm();
    await setSelect(selectIn(c, 'Provider'), 'openai');
    for (const label of ['Base URL', 'API key', 'Model', 'Vision', 'Server type']) {
      expect(labelEl(c, label), label).not.toBeNull();
    }
    expect(c.querySelectorAll('input[type="password"]')).toHaveLength(1);
    expect(c.textContent).not.toContain('Vision pre-filter');
    expect(c.textContent).toContain('Provider change not saved.');
    expect(calls.some((x) => x.method === 'PUT' || x.method === 'POST')).toBe(false);
  });

  it('Save provider PUTs the choice', async () => {
    baseLlm();
    on('PUT', '/api/integrations/llm/credentials', ({ init }) => {
      const body = JSON.parse(init.body);
      if (body.provider) llmProvider = body.provider;
      return ok({ ok: true, applied: ['provider'], managed_by_env: [], skipped_masked: [] });
    });
    const c = await renderLlm();
    await setSelect(selectIn(c, 'Provider'), 'openai');
    await click(buttonByText(c, 'Save provider'));
    const puts = calls.filter((x) => x.method === 'PUT' && x.url === '/api/integrations/llm/credentials');
    expect(puts).toHaveLength(1);
    expect(JSON.parse(puts[0].init.body)).toEqual({ provider: 'openai' });
    expect(refetchCalls.length).toBeGreaterThan(0);
    await settle();
    expect(selectIn(c, 'Provider').value).toBe('openai');
    expect(c.textContent).not.toContain('Provider change not saved.');
  });

  it('Discard returns to the saved provider', async () => {
    baseLlm();
    const c = await renderLlm();
    await setSelect(selectIn(c, 'Provider'), 'openai');
    expect(labelEl(c, 'Base URL')).not.toBeNull();
    await click(buttonByText(c, 'Discard'));
    expect(labelEl(c, 'URL')).not.toBeNull();
    expect(calls.some((x) => x.method === 'PUT')).toBe(false);
  });

  it('saved openai opens on the OpenAI-compatible editor', async () => {
    llmProvider = 'openai';
    baseLlm();
    const c = await renderLlm();
    expect(labelEl(c, 'Base URL')).not.toBeNull();
    expect(selectIn(c, 'Provider').value).toBe('openai');
    expect(c.textContent).not.toContain('Provider change not saved.');
  });

  it('env-managed provider is read-only', async () => {
    baseLlm({ envManaged: true });
    const c = await renderLlm();
    expect(selectIn(c, 'Provider').disabled).toBe(true);
    expect(c.textContent).toContain('managed by env (read-only)');
  });

  it('provider save managed by env says so', async () => {
    baseLlm();
    on('PUT', '/api/integrations/llm/credentials', () => ok({
      ok: true, applied: [], managed_by_env: ['llm_provider'], skipped_masked: [],
    }));
    const c = await renderLlm();
    await setSelect(selectIn(c, 'Provider'), 'openai');
    await click(buttonByText(c, 'Save provider'));
    expect(c.textContent).toContain('Provider is managed by env (unchanged).');
  });

  it('provider save failure is shown', async () => {
    baseLlm();
    on('PUT', '/api/integrations/llm/credentials', () => fail(400, 'provider must be ollama or openai'));
    const c = await renderLlm();
    await setSelect(selectIn(c, 'Provider'), 'openai');
    await click(buttonByText(c, 'Save provider'));
    expect(c.textContent).toContain('Save failed: provider must be ollama or openai');
  });

  it('provider config failure falls back to Ollama', async () => {
    baseLlm({ llmStatus: 500 });
    const c = await renderLlm();
    expect(c.textContent).toContain("Couldn't load provider");
    expect(labelEl(c, 'URL')).not.toBeNull();
    expect(c.textContent).toContain('Vision pre-filter');
  });
});

describe('CredentialEditor for openai (#616)', () => {
  function savedOpenai(values = {}) {
    llmProvider = 'openai';
    openaiValues = { ...openaiValues, ...values };
    baseLlm();
  }

  it('URL hint lists the three examples', async () => {
    savedOpenai();
    const c = await renderLlm();
    expect(c.textContent).toContain('http://unsloth:8000/v1');
    expect(c.textContent).toContain('https://api.openai.com/v1');
    expect(c.textContent).toContain('https://openrouter.ai/api/v1');
  });

  it('hosted profile shows what is sent', async () => {
    savedOpenai({ url: 'https://openrouter.ai/api/v1', server_type: 'auto' });
    const c = await renderLlm();
    expect(c.textContent).toContain(SENT);
  });

  it('local profile hides what is sent', async () => {
    savedOpenai({ url: 'http://unsloth:8000/v1', server_type: 'auto' });
    const c = await renderLlm();
    expect(c.textContent).not.toContain('Sent to this service:');
  });

  it('server type draft drives the sent line', async () => {
    savedOpenai({ url: 'http://unsloth:8000/v1' });
    const c = await renderLlm();
    expect(c.textContent).not.toContain('Sent to this service:');
    await setSelect(selectIn(c, 'Server type'), 'openai');
    expect(c.textContent).toContain(SENT);
  });

  it('typed hosted URL drives the sent line', async () => {
    savedOpenai({ url: 'http://unsloth:8000/v1' });
    const c = await renderLlm();
    expect(c.textContent).not.toContain('Sent to this service:');
    setValue(inputIn(c, 'Base URL'), 'https://api.openai.com/v1');
    await settle();
    expect(c.textContent).toContain(SENT);
  });

  it('vision same stays same', async () => {
    savedOpenai({ vision: 'same' });
    const c = await renderLlm();
    expect(selectIn(c, 'Vision').value).toBe('same');
    expect(c.querySelector('input[placeholder="vision model id"]')).toBeNull();
  });

  it('env-managed server type is read-only', async () => {
    llmProvider = 'openai';
    openaiValues = { ...openaiValues, server_type: 'openrouter' };
    baseLlm();
    handlers = handlers.filter((h) => h.url !== '/api/integrations/openai/config');
    openaiConfig({ envManaged: { server_type: true } });
    const c = await renderLlm();
    expect(labelEl(c, 'Server type').querySelector('select')).toBeNull();
    expect(c.textContent).toContain('managed by env (read-only)');
  });

  it('server type options', async () => {
    savedOpenai();
    const c = await renderLlm();
    expect([...selectIn(c, 'Server type').options].map((o) => o.value))
      .toEqual(['auto', 'local', 'openai', 'openrouter']);
  });

  it('vision shows a saved model id as another model', async () => {
    savedOpenai({ vision: 'my-vl' });
    const c = await renderLlm();
    expect(selectIn(c, 'Vision').value).toBe('__other__');
    expect(c.querySelector('input[placeholder="vision model id"]').value).toBe('my-vl');
  });

  it('blank other vision is not a change', async () => {
    savedOpenai({ vision: 'off' });
    const c = await renderLlm();
    await setSelect(selectIn(c, 'Vision'), '__other__');
    expect(c.querySelector('input[placeholder="vision model id"]')).not.toBeNull();
    expect(selectIn(c, 'Vision').value).toBe('__other__');
    expect(c.textContent).not.toContain('unsaved change');
    expect(buttonByText(c, 'Save changes')).toBeNull();
  });

  it('typed vision model saves', async () => {
    savedOpenai({ vision: 'off' });
    on('PUT', '/api/integrations/openai/credentials', () => ok({
      ok: true, applied: ['vision'], managed_by_env: [], skipped_masked: [],
    }));
    const c = await renderLlm();
    await setSelect(selectIn(c, 'Vision'), '__other__');
    setValue(c.querySelector('input[placeholder="vision model id"]'), 'qwen-vl');
    await settle();
    await click(buttonByText(c, 'Save changes'));
    const puts = calls.filter((x) => x.method === 'PUT' && x.url === '/api/integrations/openai/credentials');
    expect(puts).toHaveLength(1);
    expect(JSON.parse(puts[0].init.body)).toEqual({ vision: 'qwen-vl' });
  });

  it('test body leaves out a blank key', async () => {
    savedOpenai({ url: 'http://unsloth:8000/v1', model: 'm' });
    on('POST', '/api/integrations/openai/test', () => ok({
      ok: true, version: null, detail: '6/6 checks passed', error: null, steps: [],
    }));
    const c = await renderLlm();
    await click(buttonByText(c, 'Test connection'));
    const posts = calls.filter((x) => x.method === 'POST' && x.url === '/api/integrations/openai/test');
    expect(posts).toHaveLength(1);
    const body = JSON.parse(posts[0].init.body);
    expect(body).not.toHaveProperty('api_key');
    expect(body.url).toBe('http://unsloth:8000/v1');
    expect(body.model).toBe('m');
  });

  it('test steps render one row per step', async () => {
    savedOpenai();
    on('POST', '/api/integrations/openai/test', () => ok({
      ok: false, version: null, detail: '5/6 checks passed',
      error: 'the reply was not a JSON object with iso_code',
      steps: [
        { name: 'reachable', ok: true, detail: 'ok' },
        { name: 'key_accepted', ok: true, detail: 'ok' },
        { name: 'model_listed', ok: true, detail: 'ok' },
        { name: 'json_honoured', ok: false, detail: 'the reply was not a JSON object with iso_code' },
        { name: 'thinking_off', ok: true, detail: 'ok' },
        { name: 'vision', ok: true, detail: 'ok' },
      ],
    }));
    const c = await renderLlm();
    await click(buttonByText(c, 'Test connection'));
    const rows = [...c.querySelectorAll('[data-step]')];
    expect(rows.map((r) => r.getAttribute('data-step'))).toEqual([
      'reachable', 'key_accepted', 'model_listed', 'json_honoured', 'thinking_off', 'vision',
    ]);
    expect(c.textContent).toContain('JSON reply');
    expect(c.textContent).toContain('the reply was not a JSON object with iso_code');
  });
});

describe('TestSteps (#616)', () => {
  it('no steps renders nothing', async () => {
    const a = await render(React.createElement(TestSteps, { steps: undefined }));
    expect(a.querySelectorAll('[data-step]')).toHaveLength(0);
    const b = await render(React.createElement(TestSteps, { steps: [] }));
    expect(b.querySelectorAll('[data-step]')).toHaveLength(0);
  });

  it('unknown step name shows the name', async () => {
    const c = await render(React.createElement(TestSteps, {
      steps: [{ name: 'mystery', ok: true, detail: 'd' }],
    }));
    expect(c.textContent).toContain('mystery');
  });
});

describe('SystemPanel (#616)', () => {
  it('unload row shows for ollama', async () => {
    llmConfig();
    const c = await render(React.createElement(SystemPanel));
    expect(c.textContent).toContain('Unload Ollama model');
  });

  it('unload row hidden for openai', async () => {
    llmProvider = 'openai';
    llmConfig();
    const c = await render(React.createElement(SystemPanel));
    expect(c.textContent).not.toContain('Unload Ollama model');
  });

  it('unload row stays when the provider is unknown', async () => {
    llmConfig({ status: 500 });
    const c = await render(React.createElement(SystemPanel));
    expect(c.textContent).toContain('Unload Ollama model');
  });
});
