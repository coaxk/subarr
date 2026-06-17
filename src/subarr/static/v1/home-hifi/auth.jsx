// #238: forced-auth setup / login page. Branches on /api/auth/state:
//   needs_setup → first-run "create your account" (with the WHY + recovery)
//   else        → login
//
// Uses raw fetch (NOT apiFetch): a 401 here is an expected wrong-password, which
// the form shows inline — it must not trigger apiFetch's redirect-to-login loop.

const { useState, useEffect } = React;

function _safeNext() {
  // open-redirect guard: resolve `next` against our own origin and only accept it
  // if it stays same-origin. Parsing via URL() normalizes the sneaky cases a naive
  // prefix check misses — "//evil.com", "/\evil.com" (browsers fold "\" to "/"),
  // "https:/evil.com", etc. — all resolve to a foreign origin and get rejected.
  const raw = new URLSearchParams(window.location.search).get('next') || '/';
  try {
    const u = new URL(raw, window.location.origin);
    if (u.origin === window.location.origin) {
      return u.pathname + u.search + u.hash;
    }
  } catch {
    /* malformed → fall through to safe default */
  }
  return '/';
}

const card = {
  maxWidth: 380,
  margin: '8vh auto',
  background: 'var(--bg-1)',
  border: 'var(--border)',
  borderRadius: 'var(--radius-lg)',
  padding: '28px 26px',
  display: 'flex',
  flexDirection: 'column',
  gap: 14,
};
const input = {
  width: '100%',
  padding: '9px 11px',
  background: 'var(--bg-0)',
  border: 'var(--border)',
  borderRadius: 'var(--radius-md)',
  color: 'var(--fg-0)',
  fontSize: 'var(--text-base)',
};

export function AuthPage() {
  const [state, setState] = useState(null);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetch('/api/auth/state', { credentials: 'same-origin' })
      .then((r) => r.json())
      .then(setState)
      .catch(() => setState({ needs_setup: false, authed: false }));
  }, []);

  if (!state) {
    return <div style={{ ...card, color: 'var(--fg-2)' }}>Loading…</div>;
  }

  const isSetup = !!state.needs_setup;

  const submit = async (e) => {
    e.preventDefault();
    setError('');
    if (isSetup && password !== confirm) {
      setError('Passwords do not match.');
      return;
    }
    setBusy(true);
    try {
      const r = await fetch(isSetup ? '/api/auth/setup' : '/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ username, password }),
      });
      if (r.ok) {
        window.location.assign(_safeNext());
        return;
      }
      const body = await r.json().catch(() => ({}));
      setError(body.detail || (isSetup ? 'Could not create the account.' : 'invalid username or password'));
    } catch {
      setError('Network error — try again.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <form style={card} onSubmit={submit}>
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
        <img src="/static/v1/favicon.svg" alt="subarr" width="48" height="48" style={{ display: 'block' }} />
        <div style={{ fontSize: 'var(--text-h2)', fontWeight: 700, color: 'var(--fg-0)' }}>
          {isSetup ? 'Welcome to subarr' : 'Sign in to subarr'}
        </div>
      </div>

      {isSetup && (
        <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)', lineHeight: 1.5 }}>
          subarr now requires a login by default — a security hardening that matches Sonarr and
          Radarr. Create an admin account to continue. (Locked out later? Recovery options are in the{' '}
          <a
            href="https://github.com/coaxk/subarr#authentication"
            target="_blank"
            rel="noopener noreferrer"
            style={{ color: 'var(--violet-400)' }}
          >
            README
          </a>
          .)
        </div>
      )}

      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 'var(--text-sm)' }}>
        Username
        <input
          style={input}
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
          autoFocus
          required
        />
      </label>
      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 'var(--text-sm)' }}>
        Password
        <input
          style={input}
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete={isSetup ? 'new-password' : 'current-password'}
          required
        />
      </label>
      {isSetup && (
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 'var(--text-sm)' }}>
          Confirm password
          <input
            style={input}
            type="password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            autoComplete="new-password"
            required
          />
        </label>
      )}

      {error && (
        <div style={{ fontSize: 'var(--text-sm)', color: 'var(--error-400, #f87171)' }}>{error}</div>
      )}

      <button className="btn violet" type="submit" disabled={busy} style={{ marginTop: 4 }}>
        {busy ? '…' : isSetup ? 'Create account' : 'Sign in'}
      </button>

      {!isSetup && (
        <details style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>
          <summary style={{ cursor: 'pointer', color: 'var(--violet-400)' }}>Forgot your password?</summary>
          <div style={{ marginTop: 8, lineHeight: 1.6 }}>
            There's no email reset — subarr is self-hosted, so recovery happens on the box itself.
            Any one of these gets you back in (none touch the database directly):
            <ol style={{ margin: '8px 0 0', paddingLeft: 18 }}>
              <li>
                <b>CLI reset</b> — set a new password:
                <br />
                <code style={{ wordBreak: 'break-all' }}>
                  docker exec subarr python -m subarr.cli set-password --username admin --password '…'
                </code>
                <br />
                or <code>… reset-auth</code> to clear it and return to first-run setup.
              </li>
              <li>
                <b>Env override</b> — set <code>SUBARR_USER</code> + <code>SUBARR_PASS</code> in your
                compose and restart; that pair always logs in.
              </li>
              <li>
                <b>Full reset</b> — set <code>SUBARR_AUTH_RESET=1</code> and restart to clear the stored
                credential and see the setup screen again.
              </li>
            </ol>
            <div style={{ marginTop: 6 }}>
              Full details in the{' '}
              <a
                href="https://github.com/coaxk/subarr#authentication"
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: 'var(--violet-400)' }}
              >
                README
              </a>
              .
            </div>
          </div>
        </details>
      )}
    </form>
  );
}
