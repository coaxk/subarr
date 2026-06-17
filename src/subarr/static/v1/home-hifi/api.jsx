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
    // The global session guard (session-guard.js, injected into every bundle)
    // wraps window.fetch and owns the user-facing "session expired" notice +
    // redirect to /login. apiFetch just throws so the caller stops parsing a
    // 401 body — it must NOT redirect here too, or it would preempt the
    // guard's notice with an instant jump.
    throw new Error('unauthenticated');
  }
  return r;
}
