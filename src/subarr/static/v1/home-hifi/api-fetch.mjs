// #198: transparent API-key injection for the bundled UI.
//
// When SUBARR_API_KEY is set, every /api/* call needs X-Api-Key. Rather than
// touch 160+ fetch() call sites, we patch window.fetch ONCE: it fetches the
// key from the same-origin-gated /api/ui-bootstrap handout, then stamps
// X-Api-Key on same-origin /api/* requests. When no key is configured the
// handout returns "" and the wrapper is a pure pass-through.
//
// Imported for side effects by chrome.jsx (every page loads chrome), so this
// runs once per page before any data fetch resolves.

(function installApiKeyFetch() {
  if (typeof window === 'undefined' || window.__subarrApiFetchInstalled) return;
  window.__subarrApiFetchInstalled = true;

  const original = window.fetch.bind(window);

  // Bootstrap the key. A real same-origin fetch carries Sec-Fetch-Site:
  // same-origin automatically (the browser sets it; it's a forbidden header
  // we can't forge), so this passes the route's same-origin gate. Resolves
  // once; failures fall back to no key (the no-key install path).
  let apiKey = '';
  const keyReady = original('/api/ui-bootstrap', { credentials: 'same-origin' })
    .then((r) => (r.ok ? r.json() : { api_key: '' }))
    .then((d) => { apiKey = (d && d.api_key) || ''; })
    .catch(() => { apiKey = ''; });

  function pathOf(input) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    if (!url) return '';
    if (url.startsWith('/')) return url;
    try { return new URL(url, window.location.origin).pathname; } catch { return ''; }
  }

  window.fetch = async function patchedFetch(input, init) {
    const path = pathOf(input);
    // Only same-origin /api/* needs the key; skip the bootstrap itself.
    if (!path.startsWith('/api/') || path === '/api/ui-bootstrap') {
      return original(input, init);
    }
    await keyReady;
    if (!apiKey) return original(input, init);
    const opts = { ...(init || {}) };
    // Preserve caller headers (Headers instance, array, or plain object).
    const h = new Headers((opts.headers) || (typeof input === 'object' ? input.headers : undefined) || {});
    h.set('X-Api-Key', apiKey);
    opts.headers = h;
    return original(input, opts);
  };
})();
