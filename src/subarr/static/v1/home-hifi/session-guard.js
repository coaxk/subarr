// #238 follow-up: global session-expiry guard.
//
// Background polls and in-page actions call the API with the session cookie.
// When the session expires, those calls 401 — and a bare fetch() that ignores
// status just fails silently, so the user clicks around and "nothing happens"
// until a full refresh finally bounces them to /login. That's confusing.
//
// This wraps window.fetch ONCE (esbuild injects it into every page bundle) so
// any same-origin /api/* 401 surfaces a visible notice and then redirects to
// /login?next=<here>. /api/auth/* is excluded — a 401 there is an expected
// wrong-password on the login/setup page, handled inline, not a session expiry.

// Pure decision helper (unit-tested). Given a response status + the request URL
// + the current page path, should we treat this as a session expiry?
export function shouldGuardRedirect(status, url, origin, currentPath) {
  if (status !== 401) return false;
  let path;
  try {
    path = new URL(url, origin);
  } catch {
    return false;
  }
  if (path.origin !== origin) return false; // only our own API
  const p = path.pathname;
  if (!p.startsWith('/api/')) return false;
  if (p.startsWith('/api/auth/')) return false; // expected 401s (bad login)
  if (currentPath === '/login' || currentPath === '/setup') return false;
  return true;
}

function showExpiryNotice() {
  if (document.getElementById('subarr-session-expiry')) return;
  const d = document.createElement('div');
  d.id = 'subarr-session-expiry';
  d.setAttribute('role', 'alert');
  d.textContent = 'Your session expired — taking you back to sign in…';
  Object.assign(d.style, {
    position: 'fixed', top: '0', left: '0', right: '0', zIndex: '2147483647',
    padding: '12px 16px', textAlign: 'center', background: 'var(--violet-600, #7c3aed)',
    color: '#fff', font: '600 14px Inter, system-ui, sans-serif',
    boxShadow: '0 2px 8px rgba(0,0,0,0.3)',
  });
  document.body.appendChild(d);
}

export function installSessionGuard() {
  if (window.__subarrSessionGuard) return;
  window.__subarrSessionGuard = true;
  const origin = window.location.origin;
  const orig = window.fetch.bind(window);
  let redirecting = false;
  window.fetch = async function (input, init) {
    const res = await orig(input, init);
    try {
      const url = typeof input === 'string' ? input : (input && input.url) || '';
      if (!redirecting && shouldGuardRedirect(res.status, url, origin, window.location.pathname)) {
        redirecting = true;
        showExpiryNotice();
        const here = window.location.pathname + window.location.search;
        setTimeout(() => window.location.assign('/login?next=' + encodeURIComponent(here)), 1200);
      }
    } catch {
      /* never let the guard break a real request */
    }
    return res;
  };
}

// Auto-install in a real browser; skip in a non-DOM test/SSR context so unit
// tests can import shouldGuardRedirect without patching global fetch.
if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  installSessionGuard();
}
