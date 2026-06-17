// #238: shared API fetch wrapper with a global 401 interceptor.
//
// When the session expires or a restart invalidates the cookie, background
// polls (dashboard, queue, etc.) start returning 401. Without a single choke
// point the UI spins or logs errors silently. apiFetch() bounces any 401 to
// /login?next=<current> so the user lands back where they were after re-login.
//
// Route API calls through this instead of bare fetch(). Always same-origin
// (the session cookie rides along).

export async function apiFetch(url, opts = {}) {
  const r = await fetch(url, { credentials: 'same-origin', ...opts });
  if (r.status === 401) {
    const here = window.location.pathname + window.location.search;
    window.location.assign(`/login?next=${encodeURIComponent(here)}`);
    // Reject so callers don't try to parse a redirected/empty body.
    throw new Error('unauthenticated');
  }
  return r;
}
