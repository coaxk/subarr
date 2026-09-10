// @vitest-environment jsdom
//
// #479: the integration step runs the connection test by itself once the
// URL settles, and renders the server's cause hint. Drives the real
// StepIntegration through the vendored-React harness (#517).
import { describe, it, expect, beforeAll, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

let StepIntegration;
let tests;

function loadVendoredReact() {
  for (const name of ['react.production.min.js', 'react-dom.production.min.js']) {
    const url = path.resolve(process.cwd(), 'src/subarr/static/v1/vendor', name);
    (0, eval)(fs.readFileSync(url, 'utf8'));
  }
}

beforeAll(async () => {
  loadVendoredReact();
  ({ StepIntegration } = await import('../onboarding.jsx'));
});

beforeEach(() => {
  tests = [];
});

afterEach(() => {
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

// A host that owns the progress and answers onTest, like OnboardingPage does.
function Host({ initial, result }) {
  const [progress, setProgress] = React.useState(initial);
  const [testResult, setTestResult] = React.useState(null);
  const setField = (k, v) => setProgress((p) => ({ ...p, [k]: v }));
  const onTest = async () => {
    tests.push(progress.subgen_url);
    setTestResult(result);
  };
  return React.createElement(StepIntegration, {
    step: { id: 'subgen', service: 'subgen', optional: true },
    progress, setField, testResult, onTest, isTesting: false,
    autoTestDelayMs: 30,
  });
}

async function render(initial, result) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  ReactDOM.createRoot(container).render(React.createElement(Host, { initial, result }));
  await until(() => container.querySelector('input'), { what: 'the URL input' });
  return container;
}

describe('StepIntegration auto-tests the URL (#479)', () => {
  it('tests once the URL settles, without the button being clicked', async () => {
    const c = await render({ subgen_url: 'http://subgen:9000' }, { ok: true, detail: 'subarr-subgen' });
    await until(() => tests.length === 1, { what: 'the auto test' });
    expect(tests).toEqual(['http://subgen:9000']);
    await new Promise((r) => setTimeout(r, 120));
    expect(tests, 'the same URL was tested again with no change').toHaveLength(1);
  });

  it('does not test an empty or unfinished URL', async () => {
    await render({ subgen_url: 'http://' }, { ok: true });
    await new Promise((r) => setTimeout(r, 120));
    expect(tests).toHaveLength(0);
  });

  it('renders the server hint under a failed test', async () => {
    const c = await render(
      { subgen_url: 'http://subgen:9001' },
      { ok: false, error: 'subgen not reachable (refused)', cause: 'refused', hint: 'Nothing is listening on that port; check the port and that subgen is running.' },
    );
    await until(() => tests.length === 1, { what: 'the auto test' });
    await until(() => /Nothing is listening on that port/.test(c.textContent), { what: 'the hint' });
    expect(c.textContent).toContain('subgen not reachable (refused)');
  });
});
