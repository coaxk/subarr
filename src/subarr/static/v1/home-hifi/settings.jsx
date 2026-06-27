// Settings — live status + actions.
// Sections:
//   - Integrations rail with live online/version/badges per service
//   - Detail panel per integration with an inline credential editor
//     (#75: URL / API key / Plex token), Test connection + raw badges
//   - System actions: Restart subarr, Plex scan, Bazarr sync-disk, Refresh updates
//   - Telemetry transparency: install_id + opt-in toggle + last ping
//   - Updates: current/latest per product + Refresh now
//
// #75: credentials are editable in-app. Each integration's config is read
// from GET /api/integrations/{name}/config (per-field env_managed +
// masked secret), edited behind a dirty bar, optionally test-connected,
// and saved via PUT /api/integrations/{name}/credentials — which persists
// below env and rebuilds the client live (no restart). env-managed fields
// render read-only with a "managed by env" note (env stays authoritative).

import { SectionCard, StatusDot, LangTag } from './atoms.jsx';
import { apiFetch } from './api.jsx';
import { RailFooter } from './chrome.jsx';
// #75: reuse the wizard's form primitives so the in-app credential editor
// matches onboarding exactly (same input styling + test-result chip).
import { FormRow, TextInput, TestResult } from './onboarding.jsx';
// #134: multi-library management (shared with the onboarding paths step).
import { LibrariesEditor } from './libraries-editor.jsx';
import { InstancesEditor } from './instances-editor.jsx';
import { SubgenSetupFlow } from './subgen-setup.jsx';
import {
  deriveTitle, groupRulesAlphabetically, activeLadderLetters,
} from './lang-rules-util.mjs';
import { instanceSubRows } from './instance-health-util.mjs';

const { useState, useEffect, useCallback, useMemo } = React;

// ─── Live data hooks ─────────────────────────────────────────────
function useLiveHealth(intervalMs = 10000) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchOnce = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setLoading(true);
    try {
      const r = await fetch('/api/integrations/health', { credentials: 'same-origin' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      setData(d); setError(null);
    } catch (e) {
      setError((prev) => (data ? prev : e));
      // eslint-disable-next-line no-console
      console.debug('health fetch failed:', e);
    } finally {
      if (!silent) setLoading(false);
    }
  }, [data]);

  useEffect(() => {
    let cancelled = false;
    let timer = null;
    async function tick() {
      if (cancelled) return;
      await fetchOnce({ silent: true });
      if (!cancelled) timer = setTimeout(tick, intervalMs);
    }
    (async () => {
      await fetchOnce();
      if (!cancelled) timer = setTimeout(tick, intervalMs);
    })();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs]);

  return { data, loading, error, refetch: fetchOnce };
}

// #378: per-instance reachability for the Integrations summary tiles. Separate
// from useLiveHealth because the fan-out probe (one call per instance) can be
// slower than the single-default /api/integrations/health; the tiles render
// immediately and fold in per-instance sub-dots when this resolves. Returns the
// raw health array (or [] on failure — the tiles simply show no sub-rows).
function useInstancesHealth(intervalMs = 15000) {
  const [rows, setRows] = useState([]);
  useEffect(() => {
    let cancelled = false;
    let timer = null;
    const tick = async () => {
      try {
        const r = await fetch('/api/instances/health', { credentials: 'same-origin' });
        const d = r.ok ? await r.json() : null;
        if (!cancelled && d) setRows(d.health || []);
      } catch {
        /* leave last-known rows in place */
      }
      if (!cancelled) timer = setTimeout(tick, intervalMs);
    };
    tick();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [intervalMs]);
  return rows;
}

function useTelemetryState() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const refetch = useCallback(async () => {
    try {
      const r = await fetch('/api/telemetry/state', { credentials: 'same-origin' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json()); setError(null);
    } catch (e) { setError(e); }
  }, []);
  useEffect(() => { refetch(); }, [refetch]);
  return { data, error, refetch };
}

function useUpdatesState() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const refetch = useCallback(async () => {
    try {
      const r = await fetch('/api/updates', { credentials: 'same-origin' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json()); setError(null);
    } catch (e) { setError(e); }
  }, []);
  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await fetch('/api/updates/refresh', { method: 'POST', credentials: 'same-origin' });
      await refetch();
    } finally { setRefreshing(false); }
  }, [refetch]);
  useEffect(() => { refetch(); }, [refetch]);
  return { data, error, refresh, refreshing };
}

// ─── Primitives ──────────────────────────────────────────────────
// SectionCard now lives in atoms.jsx (#213) — same shape, single
// source of truth across pages.

function Row({ label, value, hint, control }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, padding: '6px 0', borderBottom: '1px solid var(--bg-3)' }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 'var(--text-md)', color: 'var(--fg-0)' }}>{label}</div>
        {hint && <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)', marginTop: 2 }}>{hint}</div>}
      </div>
      {control || <span className="mono" style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-1)', maxWidth: 360, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{value ?? '—'}</span>}
    </div>
  );
}

// #75: which editable fields each integration exposes + their human labels.
// Mirrors the backend _CREDENTIAL_FIELDS schema; the editor renders one
// FormRow per entry. Keep in sync with routers/integrations.py.
const CREDENTIAL_SCHEMA = {
  bazarr:   [{ key: 'url', label: 'URL', secret: false }, { key: 'api_key', label: 'API key', secret: true }],
  sonarr:   [{ key: 'url', label: 'URL', secret: false }, { key: 'api_key', label: 'API key', secret: true }],
  radarr:   [{ key: 'url', label: 'URL', secret: false }, { key: 'api_key', label: 'API key', secret: true }],
  tautulli: [{ key: 'url', label: 'URL', secret: false }, { key: 'api_key', label: 'API key', secret: true }],
  plex:     [{ key: 'url', label: 'URL', secret: false }, { key: 'token', label: 'Plex token', secret: true }],
  subgen:   [{ key: 'url', label: 'URL', secret: false }],
  ollama:   [{ key: 'url', label: 'URL', secret: false }, { key: 'model', label: 'Model', secret: false }],
};

// #75: inline credential editor. Reads GET /config for per-field
// env_managed + masked secret, edits behind a dirty bar, tests the
// connection, and saves via PUT /credentials (persists below env +
// rebuilds the client live). env-managed fields are read-only.
function CredentialEditor({ integrationId, refetchHealth }) {
  const schema = CREDENTIAL_SCHEMA[integrationId];
  const [config, setConfig] = useState(null);    // { fields: {key: {env_managed, has_value, value, secret}} }
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState({});         // user-edited values, keyed by field
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState(null);   // null | {ok, text} | {err}

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(`/api/integrations/${integrationId}/config`, { credentials: 'same-origin' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setConfig(await r.json());
      setDraft({});
      setTestResult(null);
      setSaveMsg(null);
    } catch (e) {
      setConfig({ error: String(e.message || e) });
    } finally {
      setLoading(false);
    }
  }, [integrationId]);
  useEffect(() => { load(); }, [load]);

  if (!schema) return null;
  if (loading) {
    return <SectionCard label="Connection"><div style={{ padding: 12, color: 'var(--fg-2)' }}>Loading…</div></SectionCard>;
  }
  if (!config || config.error) {
    return <SectionCard label="Connection"><div style={{ padding: 12, color: 'var(--fg-2)' }}>Couldn't load config: {config?.error || 'unknown'}</div></SectionCard>;
  }

  const fields = config.fields || {};
  // Editable fields the user actually changed (env-managed ones can't be in draft).
  const dirtyKeys = Object.keys(draft).filter(k => {
    const meta = fields[k] || {};
    if (meta.env_managed) return false;
    // For URLs, dirty = different from current. For secrets, any non-empty entry is a change.
    if (meta.secret) return (draft[k] || '') !== '';
    return (draft[k] || '') !== (meta.value || '');
  });
  const isDirty = dirtyKeys.length > 0;
  const allEnvManaged = schema.every(f => (fields[f.key] || {}).env_managed);

  const setField = (key, val) => {
    setDraft(d => ({ ...d, [key]: val }));
    setSaveMsg(null);
  };

  // Build the request body from dirty fields + always include URL when we
  // have one (so the test/save has a target even if only the secret changed).
  const buildBody = () => {
    const body = {};
    for (const f of schema) {
      const meta = fields[f.key] || {};
      if (meta.env_managed) continue;
      const v = draft[f.key];
      if (v != null && v !== '') body[f.key] = v;
      else if (!meta.secret && meta.value) body[f.key] = meta.value; // keep current URL/model
    }
    return body;
  };

  const testConnection = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const body = buildBody();
      const r = await fetch(`/api/integrations/${integrationId}/test`, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      setTestResult(d);
    } catch (e) {
      setTestResult({ ok: false, error: String(e.message || e) });
    } finally {
      setTesting(false);
    }
  };

  const save = async () => {
    setSaving(true);
    setSaveMsg(null);
    try {
      // Only send fields the user actually edited.
      const body = {};
      for (const k of dirtyKeys) body[k] = draft[k];
      const r = await fetch(`/api/integrations/${integrationId}/credentials`, {
        method: 'PUT', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        let detail = `HTTP ${r.status}`;
        try { const j = await r.json(); if (j.detail) detail = j.detail; } catch {}
        throw new Error(detail);
      }
      const d = await r.json();
      const parts = [];
      if (d.applied?.length) parts.push(`saved ${d.applied.length} field${d.applied.length === 1 ? '' : 's'}`);
      if (d.managed_by_env?.length) parts.push(`${d.managed_by_env.length} managed by env (unchanged)`);
      setSaveMsg({ ok: true, text: parts.join(' · ') || 'no changes' });
      await load();                 // refresh masked values + env state
      refetchHealth?.({ silent: false });   // re-probe with new creds
    } catch (e) {
      setSaveMsg({ err: String(e.message || e) });
    } finally {
      setSaving(false);
    }
  };

  return (
    <SectionCard label="Connection" action={
      <button className="btn sm ghost" onClick={testConnection} disabled={testing}>
        {testing ? 'Testing…' : 'Test connection'}
      </button>
    }>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {allEnvManaged && (
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)',
            background: 'var(--bg-2)', border: 'var(--border)',
            borderRadius: 'var(--radius-md)', padding: '8px 12px' }}>
            Every field here is set via environment variables — the operator's
            config is authoritative. Edit your <span className="mono">.env</span> and restart to change them.
          </div>
        )}

        {schema.map(f => {
          const meta = fields[f.key] || {};
          if (meta.env_managed) {
            return (
              <FormRow key={f.key} label={f.label} hint="managed by env (read-only)">
                <div className="mono" style={{
                  height: 34, display: 'flex', alignItems: 'center', padding: '0 12px',
                  background: 'var(--bg-3)', border: '1px solid var(--bg-4)',
                  borderRadius: 'var(--radius-md)', color: 'var(--fg-3)',
                  fontSize: 'var(--text-md)',
                }}>
                  {meta.secret ? (meta.value || '••••') : (meta.value || '—')}
                  <span style={{ flex: 1 }} />
                  <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>🔒 env</span>
                </div>
              </FormRow>
            );
          }
          return (
            <FormRow key={f.key} label={f.label}
              hint={meta.secret && meta.has_value
                ? `currently set (${meta.value}) — leave blank to keep`
                : undefined}>
              <TextInput
                value={draft[f.key] ?? (f.secret ? '' : (meta.value || ''))}
                onChange={(v) => setField(f.key, v)}
                type={f.secret ? 'password' : 'text'}
                placeholder={f.secret
                  ? (meta.has_value ? '•••• (unchanged)' : 'paste key')
                  : (f.key === 'url' ? 'http://host:port' : '')}
              />
            </FormRow>
          );
        })}

        {testResult && <TestResult result={testResult} />}
        {saveMsg?.ok && (
          <div style={{ fontSize: 'var(--text-sm)', color: '#22c55e' }}>✓ {saveMsg.text}</div>
        )}
        {saveMsg?.err && (
          <div style={{ fontSize: 'var(--text-sm)', color: 'var(--error-500)' }}>Save failed: {saveMsg.err}</div>
        )}
      </div>

      {/* Dirty bar — appears only when there are unsaved edits. */}
      {isDirty && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12,
          marginTop: 14, paddingTop: 14, borderTop: '1px solid var(--bg-3)',
        }}>
          <span style={{ flex: 1, fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
            {dirtyKeys.length} unsaved change{dirtyKeys.length === 1 ? '' : 's'}. Saves persist across
            restarts (below any env var) and apply live — no restart.
          </span>
          <button className="btn sm ghost" onClick={() => { setDraft({}); setTestResult(null); setSaveMsg(null); }}
            disabled={saving}>Discard</button>
          <button className="btn sm" onClick={save} disabled={saving}
            style={{ background: 'var(--violet-500)', color: '#fff' }}>
            {saving ? 'Saving…' : 'Save changes'}
          </button>
        </div>
      )}
    </SectionCard>
  );
}

function Toggle({ on, onToggle, busy, label }) {
  return (
    <button
      onClick={onToggle}
      disabled={busy}
      role="switch"
      aria-checked={!!on}
      aria-label={label || (on ? 'Disable' : 'Enable')}
      style={{
        display: 'inline-block', width: 36, height: 20,
        borderRadius: 99,
        background: on ? 'var(--violet-500)' : 'var(--bg-4)',
        position: 'relative', cursor: busy ? 'wait' : 'pointer',
        transition: 'background var(--dur-fast)',
        border: 'none', padding: 0,
      }}>
      <span style={{
        position: 'absolute', top: 2,
        left: on ? 18 : 2,
        width: 16, height: 16,
        background: '#fff',
        borderRadius: '50%',
        transition: 'left var(--dur-fast) var(--ease-out)',
      }} />
    </button>
  );
}

function Stat({ label, value, color }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <span className="label">{label}</span>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span className="display num" style={{
          fontSize: 'var(--text-display-lg)', lineHeight: 1, fontWeight: 500,
          color: color || 'var(--fg-0)', letterSpacing: '-0.01em',
        }}>{value}</span>
      </div>
    </div>
  );
}

// ─── Integrations rail ───────────────────────────────────────────
const INTEGRATION_ORDER = ['bazarr', 'sonarr', 'radarr', 'plex', 'tautulli', 'subgen', 'ollama'];

// Friendly labels for integration badges. Source endpoints often
// return terse keys (Bazarr's /api/badges: 'episodes' actually means
// 'episodes-with-missing-subs', NOT total library size). Without an
// override the UI rendered the raw key, which read as misleading.
// Add entries here whenever a new badge key appears in /api/integrations/health.
const BADGE_LABELS = {
  // Bazarr /api/badges keys
  episodes: 'episodes wanted',
  movies: 'movies wanted',
  providers: 'providers',
  status: 'announcements',
  sonarr_signalr: 'sonarr signalr',
  radarr_signalr: 'radarr signalr',
  announcements: 'announcements',
  // Ollama integration probe
  models: 'models installed',
  model_names: 'top models',
  vision_model_config: 'vision model (config)',
  vision_model_resolved: 'vision model (active)',
  vision_capable: 'vision pre-filter',
};

function buildRailItems(health) {
  if (!health) return [];
  const out = [];
  const byName = {};
  for (const i of (health.integrations || [])) byName[i.name] = i;
  if (health.subgen) byName.subgen = { ...health.subgen, name: 'subgen', online: health.subgen.reachable, configured: true };
  for (const name of INTEGRATION_ORDER) {
    if (!byName[name]) continue;
    const i = byName[name];
    out.push({
      id: name, name: name.charAt(0).toUpperCase() + name.slice(1),
      status: i.online ? 'ok' : (i.configured ? 'error' : 'muted'),
      meta: i.version ? `v${i.version}` : (i.configured ? '—' : 'unconfigured'),
      raw: i,
    });
  }
  return out;
}

// #207: Integrations summary panel. Lands here when user clicks Settings/Health
// (instead of being dumped into Bazarr automatically). Tile grid showing every
// integration's status / version / key metric with click-through to detail.
// ─── Friendly header (4 panels) for the landing/summary view ─────
//
// Settings is sprawling: integrations rail + 5 top-level views + the
// existing IntegrationsSummaryPanel grid. First-time visitors land
// here without context. Same panel pattern as Home/Rules/Coverage:
// a welcome card explaining what each view is for + 4 status tiles
// summarising the system's posture.

function SettingsHeaderTile({ label, value, sub, tint, tip, href, accent }) {
  const inner = (
    <div title={tip} style={{
      flex: 1, minWidth: 0,
      background: 'var(--bg-1)',
      border: 'var(--border)',
      borderRadius: 'var(--radius-lg)',
      padding: '12px 14px',
      display: 'flex', flexDirection: 'column', gap: 8,
      cursor: href ? 'pointer' : 'default',
      textDecoration: 'none', color: 'inherit',
      transition: 'background 120ms ease, border-color 120ms ease',
    }}
      onMouseEnter={href ? (e) => e.currentTarget.style.background = 'var(--bg-2)' : undefined}
      onMouseLeave={href ? (e) => e.currentTarget.style.background = 'var(--bg-1)' : undefined}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {tint && <StatusDot kind={tint} />}
        <span className="label">{label}</span>
      </div>
      <div style={{
        fontSize: 'var(--text-h1)', lineHeight: 'var(--lh-h1)', fontWeight: 500,
        color: accent || 'var(--fg-0)', letterSpacing: '-0.01em',
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>{value}</div>
      <div style={{
        fontSize: 'var(--text-xs)', color: 'var(--fg-2)',
        minHeight: 16, lineHeight: 1.35,
      }}>{sub}</div>
    </div>
  );
  return href
    ? <a href={href} style={{ flex: 1, minWidth: 0, textDecoration: 'none', color: 'inherit' }}>{inner}</a>
    : inner;
}

function SettingsStatusRow({ rail, onView }) {
  // rail is the buildRailItems(health) output — every configured
  // integration with its status string (set at line 208: 'ok' / 'error' /
  // 'muted'). Originally I read r.meta.kind which doesn't exist on the
  // rail items — meta is a version-or-status STRING per buildRailItems,
  // not an object — so this filter always returned 0 and the tile read
  // "0/6 all healthy" on a perfectly healthy install. Fixed.
  const healthy = rail.filter(r => r.status === 'ok').length;
  const degraded = rail.filter(r => r.status === 'error').length;
  const total = rail.length;

  // Telemetry + updates + provider state are cheap one-shot fetches —
  // the dedicated panels poll them, but we want a top-level summary
  // visible before the user clicks in.
  const [tel, setTel] = useState(null);
  const [upd, setUpd] = useState(null);
  const [prov, setProv] = useState(null);
  useEffect(() => {
    fetch('/api/telemetry/state', { credentials: 'same-origin' })
      .then(r => r.ok ? r.json() : null).then(setTel).catch(() => {});
    fetch('/api/updates/state', { credentials: 'same-origin' })
      .then(r => r.ok ? r.json() : null).then(setUpd).catch(() => {});
    fetch('/api/providers/leaderboard', { credentials: 'same-origin' })
      .then(r => r.ok ? r.json() : null).then(setProv).catch(() => {});
  }, []);
  const updatesAvailable = upd && upd.products
    ? upd.products.filter(p => p.update_available).length
    : 0;
  const providerCount = prov?.providers?.length || 0;
  const telemetryOn = tel?.opted_in === true;

  return (
    <div style={{ display: 'flex', gap: 12, marginBottom: 14, maxWidth: 920 }}>
      <SettingsHeaderTile
        label="integrations"
        value={total === 0 ? '—' : `${healthy}/${total}`}
        sub={total === 0
          ? 'none configured — run onboarding'
          : (degraded > 0 ? `${degraded} need attention` : 'all healthy')}
        tint={total === 0 ? 'muted' : (degraded > 0 ? 'warn' : 'ok')}
        tip="Live health from /api/integrations/health. Click an integration in the rail to test or inspect."
      />
      <SettingsHeaderTile
        label="telemetry"
        value={tel == null ? '—' : (telemetryOn ? 'opted in' : 'off')}
        sub={tel == null
          ? 'loading…'
          : (telemetryOn ? 'anonymous aggregates sent' : 'nothing leaves your network')}
        tint={tel == null ? undefined : (telemetryOn ? 'info' : 'muted')}
        href="/settings#telemetry"
        tip="What subarr sends and how to opt in/out."
      />
      <SettingsHeaderTile
        label="updates"
        value={upd == null ? '—' : (updatesAvailable > 0 ? updatesAvailable : 'up to date')}
        sub={upd == null
          ? 'checking GitHub…'
          : (updatesAvailable > 0
              ? `new release${updatesAvailable === 1 ? '' : 's'} on GHCR`
              : 'latest version installed')}
        tint={upd == null ? undefined : (updatesAvailable > 0 ? 'violet' : 'ok')}
        accent={updatesAvailable > 0 ? 'var(--violet-400)' : undefined}
        href="/settings#updates"
        tip="Per-product version checks against GitHub releases for subarr + subarr-subgen."
      />
      <SettingsHeaderTile
        label="provider stats"
        value={providerCount === 0 ? '—' : providerCount}
        sub={providerCount === 0
          ? 'connect Bazarr + run a walk'
          : 'tracked from Bazarr history'}
        tint={providerCount > 0 ? 'info' : 'muted'}
        href="/settings#providers"
        tip="Bazarr provider success rates from your download history. Surfaces which providers actually work for your library."
      />
    </div>
  );
}

const SETTINGS_WELCOME_KEY = 'subarr.settings.welcome.dismissed';

function SettingsWelcomeCard() {
  const [dismissed, setDismissed] = useState(() => {
    try { return localStorage.getItem(SETTINGS_WELCOME_KEY) === '1'; }
    catch { return false; }
  });
  if (dismissed) return null;
  const dismiss = () => {
    try { localStorage.setItem(SETTINGS_WELCOME_KEY, '1'); } catch {}
    setDismissed(true);
  };
  const steps = [
    {
      icon: '🔌',
      title: 'Check integration health',
      copy: 'Click any tile below to see live status from /api/integrations/health and test the connection.',
      cta: { label: 'See integrations', onClick: (e) => {
        e.preventDefault();
        // Scroll to the existing IntegrationsSummaryPanel — it's right below this card.
        const el = document.querySelector('[data-integrations-grid]');
        if (el) el.scrollIntoView({ behavior: 'smooth' });
      }},
    },
    {
      icon: '🔁',
      title: 'Run an update check',
      copy: 'subarr polls GitHub every 24h. Force a check now if you just pushed an image.',
      cta: { label: 'Open Updates', href: '/settings#updates' },
    },
    {
      icon: '📊',
      title: 'Provider leaderboard',
      copy: "See which Bazarr providers actually work for YOUR library — sortable by success rate, language, and throughput.",
      cta: { label: 'See providers', href: '/settings#providers' },
    },
  ];
  return (
    <div style={{
      background: 'linear-gradient(135deg, rgba(139,92,246,0.10), rgba(34,211,161,0.05))',
      border: '1px solid rgba(139,92,246,0.30)',
      borderRadius: 'var(--radius-lg)',
      padding: '18px 20px',
      display: 'flex', flexDirection: 'column', gap: 14,
      marginBottom: 14, maxWidth: 920,
    }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <span style={{ fontSize: 22, lineHeight: 1 }}>⚙️</span>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 'var(--text-lg)', fontWeight: 600, color: 'var(--fg-0)' }}>
            Settings is where you peek under the hood.
          </div>
          <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)', marginTop: 4, lineHeight: 1.5 }}>
            Every integration's live status, telemetry knob, update checks, and provider stats. Nothing
            here is destructive — the actions are <i>tell-subarr-something</i>, not <i>edit-config</i>.
            For credentials and URLs, re-run onboarding from <b>System actions</b>.
          </div>
        </div>
        <button className="btn ghost" onClick={dismiss}
          title="Hide this card on this device."
          style={{ fontSize: 'var(--text-2xs)' }}>got it</button>
      </div>
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
        gap: 10,
      }}>
        {steps.map((s, i) => (
          <div key={i} style={{
            background: 'var(--bg-1)', border: 'var(--border)',
            borderRadius: 'var(--radius-md)', padding: 12,
            display: 'flex', flexDirection: 'column', gap: 8,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 16 }}>{s.icon}</span>
              <span style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--fg-0)' }}>
                {s.title}
              </span>
            </div>
            <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)', flex: 1 }}>
              {s.copy}
            </div>
            <div>
              <a href={s.cta.href || '#'} onClick={s.cta.onClick}
                className="btn" style={{
                  textDecoration: 'none', display: 'inline-block',
                  fontSize: 'var(--text-2xs)', padding: '4px 10px',
                  background: 'var(--violet-500)', color: '#fff',
                }}>
                {s.cta.label}
              </a>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function IntegrationsSummaryPanel({ rail, instancesHealth, onSelect }) {
  if (!rail || rail.length === 0) {
    return <div style={{ padding: 20, color: 'var(--fg-2)' }}>
      No integrations configured. Run the onboarding wizard to set them up.
    </div>;
  }
  return (
    <div style={{ maxWidth: 920, display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>
        Click any tile for live details, test connection, or per-integration actions.
      </div>
      <div data-integrations-grid style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
        gap: 12,
      }}>
        {rail.map((it) => {
          const raw = it.raw || {};
          const meta = it.meta;
          // #378: per-instance sub-dots for instanced services with >1 instance
          // (empty for single-stack — the tile stays visually identical).
          const subRows = instanceSubRows(it.id, instancesHealth);
          const badge = (() => {
            // Pull the most-interesting metric per integration name
            const n = (it.id || '').toLowerCase();
            if (n === 'bazarr') return raw.badges?.episodes != null ? `${raw.badges.episodes} eps wanted` : null;
            if (n === 'sonarr') return raw.count != null ? `${raw.count} series` : null;
            if (n === 'radarr') return raw.count != null ? `${raw.count} movies` : null;
            if (n === 'tautulli') return raw.history_rows != null ? `${raw.history_rows} plays / 30d` : null;
            if (n === 'subgen') return raw.version ? `v${raw.version}` : null;
            if (n === 'ollama') return raw.model || null;
            return null;
          })();
          return (
            <button key={it.id} onClick={() => onSelect(it.id)} style={{
              textAlign: 'left',
              background: 'var(--bg-1)', border: 'var(--border)',
              borderRadius: 'var(--radius-md)', padding: '14px 16px',
              display: 'flex', flexDirection: 'column', gap: 8,
              cursor: 'pointer',
              transition: 'border-color 120ms ease, background 120ms ease',
            }}
              onMouseEnter={(e) => e.currentTarget.style.background = 'var(--bg-2)'}
              onMouseLeave={(e) => e.currentTarget.style.background = 'var(--bg-1)'}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <StatusDot kind={it.status} />
                <span style={{ fontSize: 'var(--text-md)', fontWeight: 600, color: 'var(--fg-0)' }}>
                  {it.name}
                </span>
                <span style={{ flex: 1 }} />
                <span className="mono num" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>
                  {meta}
                </span>
              </div>
              <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
                {it.status === 'ok' && (badge || 'connected')}
                {it.status === 'error' && (raw.error || 'unreachable')}
                {it.status === 'muted' && 'not configured'}
              </div>
              {subRows.length > 0 && (
                <div style={{
                  display: 'flex', flexDirection: 'column', gap: 4,
                  marginTop: 2, paddingTop: 8, borderTop: '1px solid var(--bg-3)',
                }}>
                  {subRows.map((s) => (
                    <div key={s.id || '(default)'} title={s.label}
                      style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--text-2xs)' }}>
                      <StatusDot kind={s.kind} />
                      <span style={{ color: 'var(--fg-1)', fontWeight: 500 }}>{s.name}</span>
                      <span style={{ flex: 1 }} />
                      <span className="mono" style={{ color: 'var(--fg-3)' }}>{s.label}</span>
                    </div>
                  ))}
                </div>
              )}
              <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>open →</span>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// v1.1-J UI: provider leaderboard panel
function ProvidersPanel() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const fetchIt = async (fresh = false) => {
    setLoading(true);
    try {
      const r = await fetch('/api/providers/leaderboard' + (fresh ? '?fresh=true' : ''),
                            { credentials: 'same-origin' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      setData(d);
      setError(null);
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { fetchIt(); }, []);
  if (loading && !data) {
    return <div style={{ padding: 20, color: 'var(--fg-2)' }}>Loading provider stats…</div>;
  }
  if (error && !data) {
    return <div style={{ padding: 20, color: 'var(--error-500)' }}>
      Couldn't load: {error}
      <div style={{ marginTop: 12 }}><button className="btn" onClick={() => fetchIt(true)}>Retry</button></div>
    </div>;
  }
  if (!data?.available) {
    return <div style={{ padding: 20, color: 'var(--fg-2)' }}>
      Bazarr isn't configured — no provider data available.
    </div>;
  }
  const providers = data.providers || [];
  const fmtTime = (epoch) => epoch ? new Date(epoch * 1000).toLocaleDateString() : '—';
  return (
    <div style={{ maxWidth: 920, display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div style={{
        background: 'var(--bg-1)', border: 'var(--border)',
        borderRadius: 'var(--radius-md)', padding: '10px 14px',
        display: 'flex', alignItems: 'center', gap: 14,
        fontSize: 'var(--text-xs)', color: 'var(--fg-2)',
      }}>
        <span><b style={{ color: 'var(--fg-0)' }}>{data.history_rows}</b> downloads aggregated</span>
        <span style={{ color: 'var(--bg-5)' }}>·</span>
        <span>computed in {data.duration_s}s</span>
        <span style={{ color: 'var(--bg-5)' }}>·</span>
        <span>{data.cached ? `cache age ${data.cache_age_s}s` : 'live'}</span>
        <span style={{ flex: 1 }} />
        <button className="btn ghost" onClick={() => fetchIt(true)} disabled={loading}
          style={{ fontSize: 'var(--text-2xs)' }}>{loading ? '…' : 'refresh'}</button>
      </div>

      <div style={{ background: 'var(--bg-1)', border: 'var(--border)', borderRadius: 'var(--radius-md)', overflow: 'hidden' }}>
        <div style={{
          display: 'grid',
          gridTemplateColumns: '2fr 1fr 1fr 1fr 2fr 1.2fr',
          padding: '10px 14px',
          background: 'var(--bg-2)',
          fontSize: 'var(--text-2xs)', color: 'var(--fg-3)',
          textTransform: 'uppercase', letterSpacing: '0.08em',
          gap: 12,
        }}>
          <span>provider</span>
          <span style={{ textAlign: 'right' }}>downloads</span>
          <span style={{ textAlign: 'right' }}>avg score</span>
          <span style={{ textAlign: 'right' }}>success</span>
          <span>languages</span>
          <span style={{ textAlign: 'right' }}>last seen</span>
        </div>
        {providers.map(p => (
          <div key={p.name} style={{
            display: 'grid',
            gridTemplateColumns: '2fr 1fr 1fr 1fr 2fr 1.2fr',
            padding: '10px 14px',
            borderTop: '1px solid var(--bg-3)',
            fontSize: 'var(--text-sm)',
            alignItems: 'center', gap: 12,
            opacity: p.downloads === 0 ? 0.5 : 1,
          }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <StatusDot kind={p.throttled ? 'error' : (p.downloads > 0 ? 'ok' : 'muted')} />
              <span style={{ color: 'var(--fg-0)', fontWeight: 500 }}>{p.name}</span>
            </span>
            <span className="num mono" style={{ textAlign: 'right', color: 'var(--fg-1)' }}>{p.downloads}</span>
            <span className="num mono" style={{ textAlign: 'right', color: 'var(--fg-1)' }}>{p.downloads ? p.avg_score.toFixed(1) : '—'}</span>
            <span className="num mono" style={{
              textAlign: 'right',
              color: p.success_rate >= 90 ? '#22d3a1' :
                     p.success_rate >= 70 ? '#facc15' :
                     p.downloads ? '#ef4444' : 'var(--fg-3)'
            }}>{p.downloads ? `${p.success_rate.toFixed(0)}%` : '—'}</span>
            <span style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
              {Object.entries(p.languages || {}).slice(0, 5).map(([code, n]) => (
                <span key={code} title={`${n} downloads in ${code}`} className="mono" style={{
                  padding: '1px 6px', borderRadius: 3,
                  background: 'var(--bg-3)', fontSize: 'var(--text-2xs)', color: 'var(--fg-2)',
                }}>{code}:{n}</span>
              ))}
            </span>
            <span className="mono" style={{ textAlign: 'right', fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>
              {fmtTime(p.last_seen)}
            </span>
          </div>
        ))}
      </div>

      <div style={{
        background: 'var(--bg-1)', border: 'var(--border)',
        borderRadius: 'var(--radius-md)', padding: '12px 14px',
        fontSize: 'var(--text-xs)', color: 'var(--fg-2)', lineHeight: 1.5,
      }}>
        <b style={{ color: 'var(--fg-1)' }}>How this is built:</b> subarr aggregates Bazarr&apos;s subtitle-download
        history (last ~2000 events) by provider, tracking download count, average match score,
        success rate (downloads not subsequently blacklisted), and per-language breakdowns. <br/>
        <b style={{ color: 'var(--fg-1)' }}>Global leaderboard</b> (v1.1.1): anonymized snapshots from
        all installs aggregate at subarr.com/stats — the only place in the arr ecosystem with cross-user
        provider quality data.
      </div>
    </div>
  );
}

function SettingsRail({ items, selectedId, onSelect, systemActive, onSelectSystem, telemetryActive, onSelectTelemetry, updatesActive, onSelectUpdates, providersActive, onSelectProviders, langRulesActive, onSelectLangRules, librariesActive, onSelectLibraries, instancesActive, onSelectInstances, subgenTuningActive, onSelectSubgenTuning }) {
  // #10: render the same persistent GPU/queue/walker footer that the
  // other pages show in SubRail, so the bottom-left vitals are
  // visible everywhere including Settings. Aside becomes a flex
  // column: nav content scrolls in the middle band, footer pins to
  // the bottom.
  return (
    <aside style={{
      width: 220, flex: '0 0 220px',
      borderRight: 'var(--border)',
      background: 'var(--bg-1)',
      display: 'flex', flexDirection: 'column',
    }}>
      <div style={{
        flex: 1, minHeight: 0, overflowY: 'auto',
        padding: '18px 0 12px',
      }}>
      <div style={{ padding: '0 16px 12px', display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
        <h2 style={{ margin: 0, fontSize: 'var(--text-lg)', fontWeight: 600, color: 'var(--fg-0)' }}>Settings</h2>
        <span className="num mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>v1.5.2</span>
      </div>
      <div style={{ marginBottom: 14 }}>
        <div style={{ padding: '0 16px 6px' }}>
          <span className="label">integrations</span>
        </div>
        {items.length === 0 && (
          <div style={{ padding: 'var(--row-dense)', fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>Loading…</div>
        )}
        {items.map((it) => {
          const active = it.id === selectedId && !systemActive && !telemetryActive && !updatesActive && !providersActive && !langRulesActive && !librariesActive && !instancesActive && !subgenTuningActive;
          return (
            <button key={it.id} onClick={() => onSelect(it.id)} style={{
              display: 'flex', alignItems: 'center', gap: 8,
              width: '100%', height: 28,
              padding: '0 16px 0 14px',
              borderLeft: `2px solid ${active ? 'var(--violet-500)' : 'transparent'}`,
              background: active ? 'rgba(139,92,246,0.08)' : 'transparent',
              color: active ? 'var(--fg-0)' : 'var(--fg-1)',
              fontSize: 'var(--text-base)', fontWeight: active ? 600 : 500,
              borderTop: 'none', borderRight: 'none', borderBottom: 'none',
              cursor: 'pointer', textAlign: 'left',
            }}>
              <StatusDot kind={it.status} />
              <span style={{ flex: 1 }}>{it.name}</span>
              <span className="mono num" style={{ fontSize: 'var(--text-2xs)', color: active ? 'var(--fg-2)' : 'var(--fg-3)' }}>{it.meta}</span>
            </button>
          );
        })}
      </div>
      <div style={{ marginBottom: 14 }}>
        <div style={{ padding: '0 16px 6px' }}><span className="label">subarr</span></div>
        {[
          { id: 'providers', label: 'Providers', active: providersActive, onClick: onSelectProviders },
          { id: 'instances', label: 'Instances', active: instancesActive, onClick: onSelectInstances },
          { id: 'libraries', label: 'Libraries', active: librariesActive, onClick: onSelectLibraries },
          { id: 'lang-rules', label: 'Language rules', active: langRulesActive, onClick: onSelectLangRules },
          { id: 'subgen-tuning', label: 'Subgen tuning', active: subgenTuningActive, onClick: onSelectSubgenTuning },
          { id: 'system', label: 'System actions', active: systemActive, onClick: onSelectSystem },
          { id: 'updates', label: 'Updates', active: updatesActive, onClick: onSelectUpdates },
          { id: 'telemetry', label: 'Telemetry', active: telemetryActive, onClick: onSelectTelemetry },
        ].map((it) => (
          <button key={it.id} onClick={it.onClick} style={{
            display: 'flex', alignItems: 'center', gap: 8,
            width: '100%', height: 28, padding: '0 16px 0 14px',
            borderLeft: `2px solid ${it.active ? 'var(--violet-500)' : 'transparent'}`,
            background: it.active ? 'rgba(139,92,246,0.08)' : 'transparent',
            color: it.active ? 'var(--fg-0)' : 'var(--fg-1)',
            fontSize: 'var(--text-base)', fontWeight: it.active ? 600 : 500,
            border: 'none', borderLeftWidth: 2, cursor: 'pointer', textAlign: 'left',
          }}>{it.label}</button>
        ))}
      </div>
      </div>
      <RailFooter />
    </aside>
  );
}

// ─── Integration detail panel ────────────────────────────────────
// #171: surface the per-language SUBGEN_KWARGS_LANG_* blocks from the
// patched subgen's compose. /api/mode parses the YAML and returns
// top_level_kwargs + per_language_kwargs as already-parsed dicts. The
// whole point of subarr-subgen is THIS feature; making it visible in
// the UI is the only way users discover what's tuned vs. default.
//
// Known languages — used to render a human-friendly name beside the
// ISO 639-1 code. Not exhaustive; unknown codes still render as the
// raw code so we don't error on new additions.
const LANG_NAMES = {
  EN: 'English', JA: 'Japanese', KO: 'Korean', ZH: 'Chinese',
  ES: 'Spanish', FR: 'French', DE: 'German', IT: 'Italian',
  PT: 'Portuguese', RU: 'Russian', HI: 'Hindi', AR: 'Arabic',
  TR: 'Turkish', NL: 'Dutch', PL: 'Polish', SV: 'Swedish',
};

// The handful of Whisper kwargs users actually care about. Renders any
// other key without a tooltip — the table stays accurate even when the
// patched subgen adds new knobs we haven't documented yet.
const KWARG_HINTS = {
  beam_size: 'Search width during decoding. 5 = balanced; higher = slower + slightly better accuracy.',
  patience: 'How long beam search waits for a better hypothesis. 1.0 = neutral; >1 = more thorough.',
  length_penalty: 'Penalty against very long hypotheses. >1 favours longer; <1 favours shorter.',
  repetition_penalty: 'Penalty against repeating tokens. >1 reduces repetition. JA often benefits.',
  no_repeat_ngram_size: 'Block this-length token n-grams from repeating. 3 is common.',
  compression_ratio_threshold: 'Reject segments whose compression ratio exceeds this — catches looping output.',
  log_prob_threshold: 'Reject segments whose avg log prob falls below this. -1.0 typical.',
  no_speech_threshold: 'Treat segment as silence when no-speech prob exceeds this.',
  temperature: 'Sampling temperature schedule. List of values = fallback chain on rejection.',
  vad_filter: 'Run Silero VAD before Whisper to drop silence regions.',
  vad_parameters: 'Tunables for the VAD pre-filter.',
  condition_on_previous_text: 'Feed prior segment as context. False reduces drift on long files.',
  initial_prompt: 'Seed text passed to Whisper. Useful for proper-noun / domain biasing.',
};

function SubgenKwargsCard() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  useEffect(() => {
    let cancelled = false;
    fetch('/api/mode', { credentials: 'same-origin' })
      .then(async r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(d => { if (!cancelled) { setData(d); setLoading(false); } })
      .catch(e => { if (!cancelled) { setError(e); setLoading(false); } });
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return (
      <SectionCard label="Per-language tuning (subarr-subgen)">
        <div style={{ padding: 14, color: 'var(--fg-2)', fontSize: 'var(--text-sm)' }}>
          Reading subgen's compose…
        </div>
      </SectionCard>
    );
  }
  if (error) {
    return (
      <SectionCard label="Per-language tuning (subarr-subgen)">
        <div style={{ padding: 14, color: 'var(--fg-2)', fontSize: 'var(--text-sm)' }}>
          Couldn't read subgen's compose: <span className="mono">{String(error.message || error)}</span>.
          {' '}This panel needs <span className="mono">SUBGEN_COMPOSE_PATH</span> mounted into subarr.
        </div>
      </SectionCard>
    );
  }

  const topLevel = data.top_level_kwargs;
  const perLang = data.per_language_kwargs || [];

  return (
    <SectionCard label="Per-language tuning (subarr-subgen)">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)', lineHeight: 1.5 }}>
          subarr-subgen lets you override Whisper kwargs per source language. The patched
          worker picks the right block automatically based on the detected (or forced) audio
          language. This is the single biggest reason to run subarr-subgen over vanilla — JA
          drift and KO timing both need different kwargs from the defaults that work for EN.
          <br/>
          <span style={{ color: 'var(--fg-3)' }}>
            Read-only here. Edit <span className="mono">SUBGEN_KWARGS_LANG_&lt;CODE&gt;</span> in your
            subgen compose file, then restart the subgen container.
          </span>
        </div>

        {/* Global defaults */}
        {topLevel && (
          <div>
            <div style={{
              fontSize: 'var(--text-xs)', textTransform: 'uppercase', letterSpacing: '0.10em',
              color: 'var(--fg-3)', marginBottom: 8,
            }}>
              Global defaults · <span className="mono">SUBGEN_KWARGS</span>
            </div>
            <KwargsTable kwargs={topLevel} />
          </div>
        )}

        {/* Per-language overrides */}
        {perLang.length === 0 ? (
          <div style={{
            padding: '14px 16px', background: 'var(--bg-2)',
            border: 'var(--border)', borderRadius: 'var(--radius-md)',
            fontSize: 'var(--text-sm)', color: 'var(--fg-2)',
          }}>
            No per-language overrides set yet. Add a{' '}
            <span className="mono">SUBGEN_KWARGS_LANG_JA</span> block in your subgen compose
            file (or follow the recipes at{' '}
            <a href="https://github.com/coaxk/subarr-subgen#per-language-kwargs"
               target="_blank" rel="noopener noreferrer"
               style={{ color: 'var(--violet-400)' }}>subarr-subgen docs</a>) to start
            tuning per source language.
          </div>
        ) : (
          <div>
            <div style={{
              fontSize: 'var(--text-xs)', textTransform: 'uppercase', letterSpacing: '0.10em',
              color: 'var(--fg-3)', marginBottom: 8,
            }}>
              Per-language overrides
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {perLang.map(lk => (
                <div key={lk.code} style={{
                  background: 'var(--bg-2)',
                  border: '1px solid var(--bg-4)',
                  borderRadius: 'var(--radius-md)',
                  overflow: 'hidden',
                }}>
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 10,
                    padding: '10px 14px',
                    background: 'rgba(139,92,246,0.06)',
                    borderBottom: '1px solid var(--bg-4)',
                  }}>
                    <span className="mono" style={{
                      fontSize: 'var(--text-xs)', fontWeight: 600,
                      letterSpacing: '0.06em', color: 'var(--violet-400)',
                    }}>{lk.code}</span>
                    <span style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-0)' }}>
                      {LANG_NAMES[lk.code] || lk.code}
                    </span>
                    <span style={{ flex: 1 }} />
                    {lk.parse_error && (
                      <span style={{ fontSize: 'var(--text-xs)', color: 'var(--error-500)' }}>
                        parse error: {lk.parse_error}
                      </span>
                    )}
                  </div>
                  <div style={{ padding: '8px 14px 12px' }}>
                    {lk.parsed
                      ? <KwargsTable kwargs={lk.parsed} compact />
                      : <pre className="mono" style={{
                          margin: 0, fontSize: 'var(--text-xs)', color: 'var(--fg-2)',
                          whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                        }}>{lk.raw}</pre>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        <div style={{
          fontSize: 'var(--text-2xs)', color: 'var(--fg-3)',
          paddingTop: 4, borderTop: '1px solid var(--bg-3)',
        }}>
          source: <span className="mono">{data.compose_path}</span>
        </div>
      </div>
    </SectionCard>
  );
}

// #230: surface the CONCURRENT_TRANSCRIPTIONS knob — subgen supports N
// parallel Whisper workers but the env var is invisible to the operator
// inside subarr. We read live /api/queue to track the highest observed
// processing[] depth (a lower bound on the current value), and document
// the VRAM cost per Whisper model so users can pick N correctly.
function SubgenConcurrencyCard() {
  const [observedMax, setObservedMax] = React.useState(null);
  React.useEffect(() => {
    let cancelled = false;
    let timer = null;
    async function tick() {
      if (cancelled) return;
      try {
        const r = await fetch('/api/queue', { credentials: 'same-origin' });
        if (r.ok) {
          const d = await r.json();
          const n = (d.processing || []).length;
          setObservedMax(prev => (prev == null || n > prev) ? n : prev);
        }
      } catch { /* silent */ }
      if (!cancelled) timer = setTimeout(tick, 5000);
    }
    tick();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, []);

  const observedLabel = observedMax == null
    ? 'measuring…'
    : observedMax === 0
      ? 'no transcribes seen yet'
      : `at least ${observedMax} (observed max processing depth)`;

  return (
    <SectionCard label="Concurrent transcribes (subarr-subgen)">
      <div style={{
        fontSize: 'var(--text-sm)', color: 'var(--fg-2)', lineHeight: 1.5,
      }}>
        Subgen can run N Whisper workers in parallel. Each worker holds
        one transcribe in memory + on GPU. Bigger N = faster catch-up on
        large libraries, but every worker needs its own VRAM slice.
      </div>

      <Row label="Currently configured"
           value={observedLabel}
           hint="subarr can't read subgen's env directly — it infers from /api/queue" />
      <Row label="Env knob"
           value="CONCURRENT_TRANSCRIPTIONS"
           hint="set in your subgen-next compose, then restart the container" />

      <div style={{
        fontSize: 'var(--text-xs)', color: 'var(--fg-2)',
        background: 'var(--bg-2)', padding: 12,
        borderRadius: 'var(--radius-md)', lineHeight: 1.5,
      }}>
        <div style={{ marginBottom: 6, color: 'var(--fg-1)', fontWeight: 600 }}>
          VRAM budget per worker (float16):
        </div>
        <div className="mono" style={{ display: 'grid',
          gridTemplateColumns: '120px 1fr', gap: '2px 12px',
        }}>
          <span>tiny</span>            <span style={{ color: 'var(--fg-3)' }}>~1 GB</span>
          <span>base</span>            <span style={{ color: 'var(--fg-3)' }}>~1 GB</span>
          <span>small</span>           <span style={{ color: 'var(--fg-3)' }}>~2 GB</span>
          <span>medium</span>          <span style={{ color: 'var(--fg-3)' }}>~5 GB</span>
          <span>large / large-v3</span><span style={{ color: 'var(--fg-3)' }}>~10 GB</span>
        </div>
        <div style={{ marginTop: 8, color: 'var(--fg-3)' }}>
          Pick N so N × per-worker-VRAM &lt; total VRAM. Leave 1–2 GB
          headroom for the container itself + CUDA cache.
        </div>
      </div>
    </SectionCard>
  );
}

function KwargsTable({ kwargs, compact }) {
  const entries = Object.entries(kwargs);
  if (entries.length === 0) {
    return <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>empty</div>;
  }
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'minmax(180px, max-content) 1fr',
      gap: compact ? '4px 14px' : '6px 16px',
      fontSize: 'var(--text-xs)',
    }}>
      {entries.map(([k, v]) => (
        <React.Fragment key={k}>
          <span className="mono"
            title={KWARG_HINTS[k] || undefined}
            style={{
              color: KWARG_HINTS[k] ? 'var(--fg-1)' : 'var(--fg-2)',
              cursor: KWARG_HINTS[k] ? 'help' : 'default',
              whiteSpace: 'nowrap',
            }}>
            {k}
          </span>
          <span className="mono" style={{
            color: 'var(--fg-0)',
            wordBreak: 'break-all',
          }}>
            {typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v)}
          </span>
        </React.Fragment>
      ))}
    </div>
  );
}

// #232 vision capability surface for the Ollama integration panel.
// Polls /api/vision/status on mount, shows current state, lets user
// pull the recommended vision model with streamed NDJSON progress.
function OllamaVisionCard() {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [pullState, setPullState] = useState(null);
    // null | { running:true, pct, status } | { error } | { done }

  const refetch = async () => {
    try {
      const r = await fetch('/api/vision/status', { credentials: 'same-origin' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setStatus(await r.json());
    } catch (e) {
      setStatus({ error: e.message });
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { refetch(); }, []);

  const pull = async (name) => {
    setPullState({ running: true, status: 'starting', pct: 0 });
    try {
      const r = await fetch('/api/vision/pull', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop() || '';
        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const ev = JSON.parse(line);
            if (ev.error) { setPullState({ error: ev.error }); return; }
            const pct = ev.total && ev.completed
              ? Math.round((ev.completed / ev.total) * 100) : null;
            setPullState({ running: true, status: ev.status || '...', pct });
          } catch {}
        }
      }
      setPullState({ done: true });
      await refetch();
    } catch (e) {
      setPullState({ error: e.message });
    }
  };

  if (loading) {
    return <SectionCard label="Vision pre-filter">
      <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>Checking capability…</div>
    </SectionCard>;
  }
  if (!status || status.error) {
    return <SectionCard label="Vision pre-filter">
      <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>
        Could not query vision status: {status?.error || 'unknown'}
      </div>
    </SectionCard>;
  }
  if (!status.ollama_configured) {
    return <SectionCard label="Vision pre-filter">
      <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>
        Ollama is not configured. Configure it above to enable vision pre-filter.
      </div>
    </SectionCard>;
  }

  const active = status.vision_capable;
  const chipBg = active ? 'rgba(34, 197, 94, 0.10)' : 'rgba(245, 158, 11, 0.10)';
  const chipBorder = active ? 'rgba(34, 197, 94, 0.30)' : 'rgba(245, 158, 11, 0.30)';
  const chipFg = active ? '#22c55e' : '#f59e0b';

  return (
    <SectionCard label="Vision pre-filter" action={
      <button className="btn sm" onClick={refetch}>Refresh</button>
    }>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10,
        padding: '10px 14px',
        background: chipBg,
        border: `1px solid ${chipBorder}`,
        borderRadius: 'var(--radius-md)',
      }}>
        <span style={{ width: 8, height: 8, borderRadius: '50%', background: chipFg }} />
        <span style={{ flex: 1, fontSize: 'var(--text-sm)', color: 'var(--fg-0)', fontWeight: 600 }}>
          {active ? 'Active' : 'Inactive'}
        </span>
        {active && (
          <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-2)' }}>
            {status.vision_model_resolved}
          </span>
        )}
      </div>

      <Row label="Configured model" value={status.vision_model_config || '(none)'}
           hint={status.vision_model_config === 'auto'
             ? "Set to 'auto' — subarr picks the first vision-capable installed model"
             : 'Set OLLAMA_VISION_MODEL to change'} />
      <Row label="Resolved model"
           value={status.vision_model_resolved || 'none'}
           hint={active ? 'Used by every vision call' : 'No vision-capable model installed'} />
      <Row label="Vision-capable models installed"
           value={(status.vision_capable_installed || []).join(', ') || '(none)'} />
      <Row label="Supported families"
           value={(status.families_supported || []).join(', ')}
           hint="Any installed model starting with one of these is vision-capable" />

      {!active && !pullState && (
        <div style={{
          padding: '10px 14px',
          background: 'rgba(245, 158, 11, 0.06)',
          border: '1px solid rgba(245, 158, 11, 0.20)',
          borderRadius: 'var(--radius-md)',
          display: 'flex', alignItems: 'center', gap: 12,
        }}>
          <div style={{ flex: 1, fontSize: 'var(--text-sm)', color: 'var(--fg-1)' }}>
            No vision-capable model installed. The pre-filter is disabled but
            every other ollama feature still works with your text model.
            Recommended pull: <span className="mono">{status.suggested_pull}</span> (~5 GB).
          </div>
          <button className="btn sm" onClick={() => pull(status.suggested_pull)}>
            Pull {status.suggested_pull}
          </button>
        </div>
      )}

      {pullState?.running && (
        <div style={{ padding: '10px 14px', background: 'var(--bg-2)', borderRadius: 'var(--radius-md)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
            <span className="spinner-ring" />
            <span style={{ flex: 1, fontSize: 'var(--text-xs)', color: 'var(--fg-1)' }}>
              {pullState.status}
            </span>
            {pullState.pct != null && <span className="mono" style={{ fontSize: 'var(--text-xs)' }}>{pullState.pct}%</span>}
          </div>
          {pullState.pct != null && (
            <div style={{ height: 4, background: 'var(--bg-3)', borderRadius: 2, overflow: 'hidden' }}>
              <div style={{ width: `${pullState.pct}%`, height: '100%', background: 'var(--violet-500)' }} />
            </div>
          )}
        </div>
      )}
      {pullState?.error && (
        <div style={{ padding: 10, color: '#ef4444', fontSize: 'var(--text-sm)' }}>
          Pull failed: {pullState.error}
        </div>
      )}
      {pullState?.done && (
        <div style={{ padding: 10, color: '#22c55e', fontSize: 'var(--text-sm)' }}>
          Pull complete. Vision pre-filter is now active.
        </div>
      )}
    </SectionCard>
  );
}


// #111 speech-aware audio (silero VAD). Polls /api/vad/status; the enable
// toggle persists via /api/vad/config (#112 config layer), and the ~2MB
// model is pulled on demand via /api/vad/pull-model. When off or
// undownloaded, clip selection falls back to ffmpeg silence detection.
function SpeechAudioCard() {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [pull, setPull] = useState(null);   // null | {running} | {error} | {done}

  const refetch = async () => {
    try {
      const r = await fetch('/api/vad/status', { credentials: 'same-origin' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setStatus(await r.json());
    } catch (e) { setStatus({ error: e.message }); }
    finally { setLoading(false); }
  };
  useEffect(() => { refetch(); }, []);

  const toggle = async () => {
    if (!status || status.env_controlled || busy) return;
    setBusy(true);
    try {
      const r = await fetch('/api/vad/config', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !status.enabled }),
      });
      if (r.ok) setStatus(await r.json());
    } finally { setBusy(false); }
  };

  const download = async () => {
    setPull({ running: true });
    try {
      const r = await fetch('/api/vad/pull-model', { method: 'POST', credentials: 'same-origin' });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        setPull({ error: d.detail || `HTTP ${r.status}` });
        return;
      }
      setPull({ done: true });
      await refetch();
    } catch (e) { setPull({ error: e.message }); }
  };

  const muted = { fontSize: 'var(--text-sm)', color: 'var(--fg-2)' };
  if (loading) return <SectionCard label="Speech-aware audio"><div style={muted}>Checking…</div></SectionCard>;
  if (!status || status.error) return <SectionCard label="Speech-aware audio"><div style={muted}>Could not query: {status?.error || 'unknown'}</div></SectionCard>;
  if (!status.runtime_present) return <SectionCard label="Speech-aware audio"><div style={muted}>The speech-detection runtime isn't included in this build.</div></SectionCard>;

  const active = status.available;
  const chipFg = active || status.model_present ? '#22c55e' : '#f59e0b';
  const chipBg = active ? 'rgba(34,197,94,0.10)' : 'rgba(245,158,11,0.10)';
  const chipBorder = active ? 'rgba(34,197,94,0.30)' : 'rgba(245,158,11,0.30)';
  const label = !status.enabled ? 'Disabled'
    : active ? 'Active — picking clips by detecting speech'
    : 'Enabled — model not downloaded yet';

  return (
    <SectionCard label="Speech-aware audio" action={<button className="btn sm" onClick={refetch}>Refresh</button>}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px',
        background: chipBg, border: `1px solid ${chipBorder}`, borderRadius: 'var(--radius-md)' }}>
        <span style={{ width: 8, height: 8, borderRadius: '50%', background: chipFg }} />
        <span style={{ flex: 1, fontSize: 'var(--text-sm)', color: 'var(--fg-0)', fontWeight: 600 }}>{label}</span>
      </div>

      <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)', lineHeight: 1.5 }}>
        Uses silero voice-activity detection to pick audio-review clips that land on actual
        dialogue (not music or silence). When off or undownloaded, subarr falls back to
        silence detection.
      </div>

      <Row label="Enabled"
        hint={status.env_controlled ? 'Locked by SUBARR_VAD_ENABLED (env wins)' : 'Persists across restarts'}
        control={<Toggle on={status.enabled} busy={busy || status.env_controlled} onToggle={toggle} />} />
      <Row label="Model" value={status.model_present ? 'downloaded (~2 MB)' : 'not downloaded'}
        hint={status.model_path || 'silero VAD ONNX model'} />

      {!status.model_present && !pull?.running && (
        <div style={{ padding: '10px 14px', background: 'rgba(245,158,11,0.06)',
          border: '1px solid rgba(245,158,11,0.20)', borderRadius: 'var(--radius-md)',
          display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ flex: 1, fontSize: 'var(--text-sm)', color: 'var(--fg-1)' }}>
            Download the ~2 MB speech model to turn this on. Until then, clip selection uses
            silence detection.
          </div>
          <button className="btn sm" onClick={download}>Download model</button>
        </div>
      )}
      {status.model_present && !pull?.running && (
        <div><button className="btn sm" onClick={download}>Re-download model</button></div>
      )}
      {pull?.running && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px',
          background: 'var(--bg-2)', borderRadius: 'var(--radius-md)' }}>
          <span className="spinner-ring" />
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-1)' }}>Downloading speech model…</span>
        </div>
      )}
      {pull?.error && <div style={{ padding: 10, color: '#ef4444', fontSize: 'var(--text-sm)' }}>Download failed: {pull.error}</div>}
      {pull?.done && <div style={{ padding: 10, color: '#22c55e', fontSize: 'var(--text-sm)' }}>Model ready — speech detection active.</div>}
    </SectionCard>
  );
}


function IntegrationPanel({ rail, refetchHealth }) {
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [syncing, setSyncing] = useState(false);

  if (!rail) {
    return (
      <div style={{ padding: 40, textAlign: 'center', color: 'var(--fg-2)' }}>
        Select an integration to view its status.
      </div>
    );
  }
  const i = rail.raw || {};

  const testConnection = async () => {
    setTesting(true);
    setTestResult({ state: 'pending' });
    try {
      // The honest "test connection" against a running v1 build is just
      // re-fetch the health endpoint; subarr's health probe already
      // exercises each integration. We surface the per-integration
      // online flag from the refreshed payload.
      await refetchHealth({ silent: false });
      // Also force-refresh the dashboard cache so the dashboard's
      // integrations tiles reflect the new state immediately, rather
      // than carrying the stale "offline" for up to 30s (cache TTL).
      // Fire-and-forget — we don't block the test UI on this.
      fetch('/api/home/dashboard?fresh=true', { credentials: 'same-origin' })
        .catch(() => {});
      setTestResult({ state: 'ok', at: Date.now() });
    } catch (e) {
      setTestResult({ state: 'error', error: e.message, at: Date.now() });
    } finally {
      setTesting(false);
    }
  };

  const bazarrSyncDisk = async () => {
    setSyncing(true);
    try {
      const r = await fetch('/api/bazarr/sync-disk', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      if (!r.ok) {
        let detail = `HTTP ${r.status}`;
        try { const j = await r.json(); if (j.detail) detail = j.detail; } catch {}
        throw new Error(detail);
      }
      alert('Bazarr sync-disk triggered.');
    } catch (e) {
      alert(`Bazarr sync-disk failed: ${e.message}`);
    } finally {
      setSyncing(false);
    }
  };

  const chipState = testResult?.state || (i.online ? 'ok' : 'error');
  const chipColor = chipState === 'pending' ? 'var(--warn-500)'
    : chipState === 'ok' ? 'var(--success-500, var(--violet-400))'
    : 'var(--error-500)';
  const chipLabel = chipState === 'pending' ? 'Testing…'
    : chipState === 'ok' ? 'Connected'
    : 'Unreachable';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 820 }}>
      {/* Status strip */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 16,
        padding: '14px 18px',
        background: chipState === 'ok' ? 'rgba(52,211,153,0.04)' : chipState === 'pending' ? 'rgba(245,158,11,0.06)' : 'rgba(239,68,68,0.04)',
        border: `1px solid ${chipColor}`,
        borderRadius: 'var(--radius-lg)',
      }}>
        <StatusDot kind={chipState === 'ok' ? 'ok' : chipState === 'pending' ? 'warn' : 'error'} size="lg" pulse={chipState !== 'error'} />
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 'var(--text-md)', color: 'var(--fg-0)', fontWeight: 600 }}>
            {chipLabel} · {rail.name} {i.version ? <span className="mono">v{i.version}</span> : null}
          </div>
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)', marginTop: 2 }}>
            configured: <span className="mono">{i.configured ? 'yes' : 'no'}</span> · online: <span className="mono">{i.online ? 'yes' : 'no'}</span>
          </div>
        </div>
        <button className="btn sm" onClick={testConnection} disabled={testing}>
          {testing ? 'Testing…' : '↻ Test connection'}
        </button>
      </div>

      {/* Badges (per-integration freshness counts).
          Friendly labels — Bazarr's /api/badges returns raw keys like
          'episodes' that are actually 'wanted-episodes' counts; the
          raw label confused users into thinking it was total library
          size. Override known keys here. */}
      {i.badges && (
        <SectionCard label="Live data">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 18 }}>
            {Object.entries(i.badges).map(([k, v]) => (
              <Stat key={k} label={BADGE_LABELS[k] || k.replace(/_/g, ' ')} value={typeof v === 'number' ? v.toLocaleString() : String(v)} />
            ))}
          </div>
        </SectionCard>
      )}

      {/* subgen capabilities (only present on the subgen panel) */}
      {rail.id === 'subgen' && (
        <SectionCard label="Capabilities">
          <Row label="queue endpoint" value={i.has_queue ? 'available' : 'missing'} />
          <Row label="batch endpoint" value={i.has_batch ? 'available' : 'missing'} />
          <Row label="subarr-subgen patch stack" value={i.is_subarr_subgen ? 'detected' : 'vanilla subgen'} />
          <Row label="compat mode" value={i.compat_mode ? 'on' : 'off'} hint="when on, subarr serialises scans for vanilla subgens" />
        </SectionCard>
      )}

      {/* #232 vision capability card — only on the Ollama panel.
          Shows whether the vision pre-filter is currently active,
          which model it resolved to, and offers a one-click pull
          of qwen2.5vl:7b when no vision-capable model is installed. */}
      {rail.id === 'ollama' && <OllamaVisionCard />}

      {/* Bazarr-specific action */}
      {rail.id === 'bazarr' && (
        <SectionCard label="Maintenance" action={
          <button className="btn sm" onClick={bazarrSyncDisk} disabled={syncing}>
            {syncing ? 'Syncing…' : 'Sync disk now'}
          </button>
        }>
          <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>
            Triggers Bazarr's "scan disk" task so it re-reads any subtitle files written out-of-band.
          </div>
        </SectionCard>
      )}

      {/* #171: subgen-only — surface the per-language SUBGEN_KWARGS_LANG_*
          tuning. This is subarr-subgen's anchor differentiator vs vanilla
          subgen and was completely invisible in the UI. Pulls from GET
          /api/mode which already parses the compose file. */}
      {rail.id === 'subgen' && <SubgenKwargsCard />}

      {/* #230: subgen-only — concurrent transcribes knob. Reads max
          observed Processing[] depth from /api/queue as a lower bound
          on the current CONCURRENT_TRANSCRIPTIONS env. Static
          documentation row explains how to change it + the VRAM
          implications per Whisper model size. */}
      {rail.id === 'subgen' && <SubgenConcurrencyCard />}

      {/* #75: inline credential editor — only for integrations with an
          editable schema (every one except none currently). Reads
          GET /config, edits behind a dirty bar, tests + saves live. */}
      {CREDENTIAL_SCHEMA[rail.id]
        ? <CredentialEditor integrationId={rail.id} refetchHealth={refetchHealth} />
        : (
          <SectionCard label="Connection">
            <Row label="Name" value={rail.name} />
            <Row label="Version" value={i.version || '—'} />
            <Row label="Online" value={i.online ? 'yes' : 'no'} />
            <Row label="Configured" value={i.configured ? 'yes' : 'no'} />
          </SectionCard>
        )}

      <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)', padding: '0 6px' }}>
        Changes save instantly and persist across restarts. An environment
        variable always wins — env-managed fields show as read-only. You can also
        {' '}<a href="/onboarding?reconfigure=1" style={{ color: 'var(--violet-400)' }}>re-run the onboarding wizard</a>.
      </div>
    </div>
  );
}

// ─── System actions panel ────────────────────────────────────────
function SystemPanel() {
  const [busy, setBusy] = useState(null);

  const run = async (label, url, confirmText) => {
    if (confirmText && !window.confirm(confirmText)) return;
    setBusy(label);
    try {
      const r = await fetch(url, { method: 'POST', credentials: 'same-origin' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      alert(`${label} OK`);
    } catch (e) {
      alert(`${label} failed: ${e.message}`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 820 }}>
      <SectionCard label="System actions">
        <Row label="Restart subarr" hint="Drops the running process and restarts the container."
          control={<button className="btn sm" disabled={!!busy}
            onClick={() => run('Restart', '/api/restart', 'Restart subarr now? In-flight scans will be cancelled.')}>
            {busy === 'Restart' ? 'Restarting…' : 'Restart'}
          </button>} />
        <Row label="Trigger Plex library scan" hint="Calls /library/sections/all/refresh on the configured Plex server."
          control={<button className="btn sm" disabled={!!busy}
            onClick={() => run('Plex scan', '/api/plex/scan')}>
            {busy === 'Plex scan' ? 'Scanning…' : 'Scan now'}
          </button>} />
        <Row label="Bazarr sync-disk" hint="Re-reads subtitle files written out-of-band."
          control={<button className="btn sm" disabled={!!busy}
            onClick={() => run('Bazarr sync-disk', '/api/bazarr/sync-disk')}>
            {busy === 'Bazarr sync-disk' ? 'Syncing…' : 'Sync now'}
          </button>} />
        <Row label="Unload Ollama model"
          hint="Frees VRAM by evicting the currently-loaded Ollama model. Useful before a heavy subgen batch — Whisper needs ~5GB VRAM."
          control={<button className="btn sm" disabled={!!busy}
            onClick={async () => {
              setBusy('Unload');
              try {
                const r = await fetch('/api/enrichment/unload', { method: 'POST' });
                const d = await r.json();
                const freed = (d.vram_before_mib || 0) - (d.vram_after_mib || 0);
                alert(freed > 50 ? `Unloaded ${d.model}. Freed ~${freed} MiB VRAM.`
                                 : `Unloaded ${d.model}. (Model wasn't resident — no VRAM freed.)`);
              } catch (e) { alert(`Failed: ${e.message}`); }
              finally { setBusy(null); }
            }}>
            {busy === 'Unload' ? 'Unloading…' : 'Unload'}
          </button>} />
      </SectionCard>

      <SpeechAudioCard />

      <SectionCard label="Onboarding">
        <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>
          Re-run the setup wizard to change connection details or library paths.
        </div>
        <div>
          <a href="/onboarding?reconfigure=1" className="btn sm" style={{ textDecoration: 'none' }}>Open onboarding wizard</a>
        </div>
      </SectionCard>

      <ApiKeysCard />
      <LoginSecurityCard />
    </div>
  );
}

// ─── Login security (#260) ───────────────────────────────────────
// Read-only view of the brute-force throttle (configured via env vars).
function LoginSecurityCard() {
  const [cfg, setCfg] = useState(null);
  useEffect(() => {
    apiFetch('/api/auth/throttle-config')
      .then((r) => (r.ok ? r.json() : null)).then(setCfg).catch(() => {});
  }, []);
  if (!cfg) return null;
  const list = (a) => (a && a.length ? a.join(', ') : 'none');
  const row = (label, value, env) => (
    <tr style={{ borderTop: 'var(--border)' }}>
      <td style={{ padding: '6px', color: 'var(--fg-2)', whiteSpace: 'nowrap' }}>{label}</td>
      <td style={{ padding: '6px', fontFamily: 'monospace' }}>{value}</td>
      <td style={{ padding: '6px', color: 'var(--fg-2)', fontSize: 'var(--text-xs)' }}>{env}</td>
    </tr>
  );
  return (
    <SectionCard label="Login security">
      <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)', lineHeight: 1.5 }}>
        Failed sign-ins are rate-limited per client IP — after {cfg.max_attempts} failures within{' '}
        {cfg.window_s}s that IP waits briefly before trying again (never a permanent lockout).
        Configured via environment variables; shown here read-only.
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--text-sm)' }}>
        <tbody>
          {row('Max failed attempts', cfg.max_attempts, 'SUBARR_LOGIN_MAX_ATTEMPTS')}
          {row('Window (seconds)', cfg.window_s, 'SUBARR_LOGIN_WINDOW_S')}
          {row('Trusted proxies', list(cfg.trusted_proxies), 'SUBARR_TRUSTED_PROXIES')}
          {row('Never-throttle allowlist', list(cfg.allowlist), 'SUBARR_LOGIN_ALLOWLIST')}
        </tbody>
      </table>
    </SectionCard>
  );
}

// ─── API keys (#259) ─────────────────────────────────────────────
// Mint/list/revoke long-lived keys for scripts + integrations. The plaintext
// token is shown exactly once, at creation — never retrievable again.
function ApiKeysCard() {
  const [keys, setKeys] = useState(null);
  const [label, setLabel] = useState('');
  const [fresh, setFresh] = useState(null);   // {label, token} shown once
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const r = await apiFetch('/api/auth/keys');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setKeys((await r.json()).keys || []);
    } catch (e) {
      if (e.message !== 'unauthenticated') setError(e.message);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const ago = (ts) => {
    if (!ts) return 'never';
    const dt = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (dt < 60) return `${dt}s ago`;
    if (dt < 3600) return `${Math.floor(dt / 60)}m ago`;
    if (dt < 86400) return `${Math.floor(dt / 3600)}h ago`;
    return `${Math.floor(dt / 86400)}d ago`;
  };

  const create = async () => {
    const name = label.trim();
    if (!name) return;
    setBusy(true); setError('');
    try {
      const r = await apiFetch('/api/auth/keys', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label: name }),
      });
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(b.detail || `HTTP ${r.status}`);
      }
      const b = await r.json();
      setFresh({ label: b.label, token: b.token });
      setLabel('');
      await load();
    } catch (e) {
      if (e.message !== 'unauthenticated') setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (id, lbl) => {
    if (!window.confirm(`Revoke key "${lbl}"? Anything using it will stop working immediately.`)) return;
    try {
      const r = await apiFetch(`/api/auth/keys/${id}`, { method: 'DELETE' });
      if (!r.ok && r.status !== 404) throw new Error(`HTTP ${r.status}`);
      await load();
    } catch (e) {
      if (e.message !== 'unauthenticated') setError(e.message);
    }
  };

  return (
    <SectionCard label="API keys">
      <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)', lineHeight: 1.5 }}>
        Keys for scripts and integrations that call subarr's API. Each key has full
        access. Send it as an <code>X-API-Key</code> header or <code>?apikey=</code> query.
      </div>

      {fresh && (
        <div style={{
          background: 'var(--bg-2)', border: 'var(--border)', borderRadius: 'var(--radius-md)',
          padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 6,
        }}>
          <div style={{ fontSize: 'var(--text-sm)', color: 'var(--warn-400, #fbbf24)', fontWeight: 600 }}>
            Copy this now — you won't see it again.
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <code style={{ flex: 1, wordBreak: 'break-all', fontSize: 'var(--text-sm)' }}>{fresh.token}</code>
            <button className="btn sm" onClick={() => { navigator.clipboard?.writeText(fresh.token); }}>Copy</button>
            <button className="btn sm ghost" onClick={() => setFresh(null)}>Done</button>
          </div>
        </div>
      )}

      <div style={{ display: 'flex', gap: 8 }}>
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') create(); }}
          placeholder="Label (e.g. home-assistant)"
          maxLength={64}
          style={{
            flex: 1, padding: '8px 10px', background: 'var(--bg-0)', border: 'var(--border)',
            borderRadius: 'var(--radius-md)', color: 'var(--fg-0)', fontSize: 'var(--text-sm)',
          }} />
        <button className="btn sm violet" onClick={create} disabled={busy || !label.trim()}>
          {busy ? '…' : 'Generate'}
        </button>
      </div>

      {error && <div style={{ fontSize: 'var(--text-sm)', color: 'var(--error-400, #f87171)' }}>{error}</div>}

      {keys && keys.length > 0 && (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--text-sm)' }}>
          <thead>
            <tr style={{ textAlign: 'left', color: 'var(--fg-2)' }}>
              <th style={{ padding: '4px 6px' }}>Label</th>
              <th style={{ padding: '4px 6px' }}>Key</th>
              <th style={{ padding: '4px 6px' }}>Created</th>
              <th style={{ padding: '4px 6px' }}>Last used</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {keys.map((k) => (
              <tr key={k.id} style={{ borderTop: 'var(--border)' }}>
                <td style={{ padding: '6px' }}>{k.label}</td>
                <td style={{ padding: '6px', fontFamily: 'monospace' }}>sbar_…{k.last4}</td>
                <td style={{ padding: '6px', color: 'var(--fg-2)' }}>{ago(k.created_at)}</td>
                <td style={{ padding: '6px', color: 'var(--fg-2)' }}>{ago(k.last_used_at)}</td>
                <td style={{ padding: '6px', textAlign: 'right' }}>
                  <button className="btn sm ghost" onClick={() => revoke(k.id, k.label)}>Revoke</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {keys && keys.length === 0 && (
        <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>No API keys yet.</div>
      )}
    </SectionCard>
  );
}

// ─── Telemetry transparency panel ────────────────────────────────
function TelemetryPanel() {
  const { data, refetch } = useTelemetryState();
  const [busy, setBusy] = useState(false);

  const toggle = async () => {
    if (!data) return;
    setBusy(true);
    try {
      const url = data.opted_in ? '/api/telemetry/opt-out' : '/api/telemetry/opt-in';
      const r = await fetch(url, { method: 'POST', credentials: 'same-origin' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await refetch();
    } catch (e) {
      alert(`Telemetry toggle failed: ${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const sendNow = async () => {
    try {
      const r = await fetch('/api/telemetry/send-now', { method: 'POST', credentials: 'same-origin' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await refetch();
    } catch (e) {
      alert(`Send-now failed: ${e.message}`);
    }
  };

  if (!data) {
    return <div style={{ padding: 40, color: 'var(--fg-2)' }}>Loading telemetry state…</div>;
  }

  const last = data.last_payload || {};
  // #11: relative-time helper for "9m ago" hints.
  const ago = (ts) => {
    if (!ts) return '';
    const dt = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (dt < 60) return `${dt}s ago`;
    if (dt < 3600) return `${Math.floor(dt / 60)}m ago`;
    if (dt < 86400) return `${Math.floor(dt / 3600)}h ago`;
    return `${Math.floor(dt / 86400)}d ago`;
  };
  const fmt = (ts) => ts ? new Date(ts * 1000).toLocaleString() : 'never';
  // Health chip: green when telemetry is succeeding, amber when
  // failing but eventually-consistent (rate-limited / endpoint slow),
  // gray when opted out.
  const healthy = data.healthy === true;
  const healthChip = !data.opted_in
    ? { dot: 'var(--fg-3)', text: 'paused' }
    : healthy
    ? { dot: 'var(--green-500, #22c55e)', text: 'healthy' }
    : { dot: 'var(--amber-500, #f59e0b)', text: 'degraded' };
  // Sent-at for the payload card header. last_payload.sent_at is set
  // every successful transmit; missing = preview-only payload.
  const sentAt = last && last.sent_at ? fmt(last.sent_at) : null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 820 }}>
      <SectionCard label="Transparency">
        <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>
          Subarr can send an anonymous weekly install heartbeat. Nothing identifying you, your media, or your subtitle content is included. You can read exactly what's sent below.
        </div>
        <Row label="Telemetry"
          hint={data.opted_in
            ? (healthy ? 'enabled — sending OK' : 'enabled — last send failed, will retry')
            : 'disabled — nothing leaves this machine'}
          control={
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6,
                              fontSize: 'var(--text-2xs)', color: 'var(--fg-2)' }}>
                <span style={{
                  width: 8, height: 8, borderRadius: '50%',
                  background: healthChip.dot,
                }} />
                {healthChip.text}
              </span>
              <Toggle on={data.opted_in} onToggle={toggle} busy={busy} />
            </div>
          } />
        <Row label="Install ID" value={data.install_id} hint="random per-install identifier — no PII" />
        <Row label="Last ping"
             value={fmt(data.last_ping_at)}
             hint={data.last_ping_at ? ago(data.last_ping_at) : 'no successful send yet'} />
        {(() => {
          // #157 P2: crash-report transparency. Counts only — the full
          // sanitized detail is visible in the payload JSON below.
          const cc = (last && last.crash_counts_24h) || {};
          const types = Object.keys(cc).length;
          const total = Object.values(cc).reduce((a, b) => a + b, 0);
          return (
            <Row label="Crash reports (24h)"
                 value={types ? `${total} crash${total === 1 ? '' : 'es'} · ${types} type${types === 1 ? '' : 's'}` : 'none'}
                 hint="exception type + module:line + count only — never messages, tracebacks, or paths" />
          );
        })()}
        {data.last_error && (
          <Row label="Last error"
               value={data.last_error}
               hint={data.last_error_at
                 ? `${ago(data.last_error_at)} — ${fmt(data.last_error_at)}`
                 : 'timestamp unknown (pre-migration row)'} />
        )}
      </SectionCard>

      <SectionCard label={sentAt
        ? `Last payload (sent ${sentAt})`
        : 'Last payload (exactly what was sent)'} action={
        <button className="btn sm" onClick={sendNow}>Send now</button>
      }>
        <pre style={{
          margin: 0, padding: 12,
          background: 'var(--bg-2)',
          border: 'var(--border)',
          borderRadius: 'var(--radius-md)',
          fontSize: 'var(--text-xs)',
          color: 'var(--fg-1)',
          maxHeight: 320, overflow: 'auto',
          fontFamily: 'var(--font-mono)',
          whiteSpace: 'pre-wrap', wordBreak: 'break-all',
        }}>{JSON.stringify(last, null, 2)}</pre>
      </SectionCard>
    </div>
  );
}

// ─── Updates panel ───────────────────────────────────────────────
// Per-product "how do I actually update" hint, shown on the row when an
// update is available (the most common support question after the badge).
const UPDATE_HOWTO = {
  subarr: 'update: docker compose pull && docker compose up -d (on your subarr stack)',
  'subarr-subgen': 'update: docker compose pull && docker compose up -d (on your subgen stack — image ghcr.io/coaxk/subarr-subgen)',
  // #223: vanilla McCloudS/subgen (no releases — version read from subgen.py on main)
  subgen: 'update: docker compose pull && docker compose up -d (on your subgen stack). Tip: switch the image to ghcr.io/coaxk/subarr-subgen for tuned defaults + tuning-lab support.',
};

function UpdatesPanel() {
  const { data, refresh, refreshing } = useUpdatesState();

  if (!data) {
    return <div style={{ padding: 40, color: 'var(--fg-2)' }}>Loading update status…</div>;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 820 }}>
      <SectionCard label="Updates" action={
        <button className="btn sm" onClick={refresh} disabled={refreshing}>
          {refreshing ? 'Checking…' : 'Refresh now'}
        </button>
      }>
        {!data.enabled && (
          <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>Update checks are disabled.</div>
        )}
        {(data.products || []).map((p) => (
          <Row key={p.product}
            label={`${p.product} ${p.has_update ? '· update available' : ''}`}
            hint={p.last_error
              ? `error: ${p.last_error}`
              : p.has_update
                ? (UPDATE_HOWTO[p.product] || (p.latest_released_at ? `released ${new Date(p.latest_released_at * 1000).toLocaleDateString()}` : ''))
                : !p.current_version && p.latest_version
                  ? 'installed release unknown (older build does not report its release tag) · latest shown'
                  : (p.latest_released_at ? `released ${new Date(p.latest_released_at * 1000).toLocaleDateString()}` : 'no release info')}
            control={
              <span className="mono" style={{ fontSize: 'var(--text-sm)' }}>
                <span style={{ color: 'var(--fg-1)' }}>{p.current_version || '—'}</span>
                {/* compare v-stripped (the backend's update_available rule):
                    "1.5.3" vs tag "v1.5.3" is up to date, not an upgrade */}
                {p.latest_version
                  && String(p.latest_version).replace(/^v/, '') !== String(p.current_version || '').replace(/^v/, '')
                  ? (
                  <> <span style={{ color: 'var(--fg-3)' }}>→</span> <span style={{ color: p.has_update ? 'var(--violet-400)' : 'var(--fg-1)' }}>{p.latest_version}</span></>
                ) : p.latest_version ? (
                  <> <span style={{ color: 'var(--ok-500, #34d399)', fontSize: 'var(--text-xs)' }}>up to date</span></>
                ) : null}
                {p.release_notes_url && (
                  <> · <a href={p.release_notes_url} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--fg-2)' }}>notes</a></>
                )}
              </span>
            } />
        ))}
      </SectionCard>
    </div>
  );
}

// #226: manage declared series/movie audio-language rules. Lists rules
// alphabetically (shows + movies unified), with flag chips, type filter,
// internal scroll, and an A-Z ladder. Declaring happens on the Review page;
// this surface is view + revoke only.
function LangRulesPanel() {
  const [rules, setRules] = useState(null);
  const [error, setError] = useState(null);
  const [typeFilter, setTypeFilter] = useState('all'); // all|show|movie
  const listRef = React.useRef(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/audio-lang/series-intent', { credentials: 'same-origin' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const body = await r.json();
      setRules(body.items || []);
      setError(null);
    } catch (e) {
      setError(e);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const revoke = useCallback(async (prefix) => {
    if (!window.confirm(`Revoke the language rule for "${deriveTitle(prefix)}"?\n\nFuture downloads will no longer inherit it. Existing per-file verifications are kept.`)) return;
    try {
      const r = await fetch(`/api/audio-lang/series-intent?series_prefix=${encodeURIComponent(prefix)}`, {
        method: 'DELETE', credentials: 'same-origin',
      });
      // 404 = already gone; treat as success and just refresh.
      if (!r.ok && r.status !== 404) throw new Error(`HTTP ${r.status}`);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('revoke failed', e);
    }
    load();
  }, [load]);

  const filtered = (rules || []).filter((r) =>
    typeFilter === 'all' ? true : (r.media_type || 'show') === typeFilter);
  const groups = groupRulesAlphabetically(filtered);
  const active = activeLadderLetters(filtered);
  const counts = {
    all: (rules || []).length,
    show: (rules || []).filter((r) => (r.media_type || 'show') === 'show').length,
    movie: (rules || []).filter((r) => r.media_type === 'movie').length,
  };

  const jumpTo = (letter) => {
    const el = listRef.current && listRef.current.querySelector(`[data-letter="${letter}"]`);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  if (error && !rules) {
    return (
      <div style={{ padding: 20, color: 'var(--error-500)' }}>
        Couldn't load language rules: {String(error.message || error)}
        <div style={{ marginTop: 12 }}><button className="btn" onClick={load}>Retry</button></div>
      </div>
    );
  }
  if (!rules) return <div style={{ padding: 20, color: 'var(--fg-2)' }}>Loading language rules…</div>;
  if (rules.length === 0) {
    return (
      <div style={{ padding: 40, textAlign: 'center', color: 'var(--fg-2)', maxWidth: 560 }}>
        No language rules yet. On the <a href="/review">Review page</a>, tick a whole show or movie,
        pick its audio language, and check <em>"Remember for future downloads."</em>
      </div>
    );
  }

  const pills = [
    { id: 'all', label: `All · ${counts.all}` },
    { id: 'show', label: `📺 Shows · ${counts.show}` },
    { id: 'movie', label: `🎬 Movies · ${counts.movie}` },
  ];

  return (
    <div style={{ maxWidth: 820, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <div style={{ display: 'flex', gap: 6, marginBottom: 12 }}>
        {pills.map((p) => (
          <span key={p.id} role="button" tabIndex={0}
            onClick={() => setTypeFilter(p.id)}
            onKeyDown={(e) => { if (e.key === 'Enter') setTypeFilter(p.id); }}
            className={`chip ${typeFilter === p.id ? 'violet' : ''}`}
            style={{ cursor: 'pointer' }}>
            {p.label}
          </span>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 8, minHeight: 0 }}>
        <div ref={listRef} style={{ flex: 1, overflowY: 'auto', maxHeight: '62vh', paddingRight: 8 }}>
          {groups.map((g) => (
            <div key={g.letter} data-letter={g.letter}>
              <div style={{
                fontSize: 'var(--text-2xs)', color: 'var(--fg-3)',
                textTransform: 'uppercase', letterSpacing: '0.1em',
                padding: '8px 0 4px', position: 'sticky', top: 0,
                background: 'var(--bg-0)',
              }}>{g.letter}</div>
              {g.rules.map((r) => (
                <div key={r.series_prefix} style={{
                  display: 'flex', alignItems: 'center', gap: 11,
                  padding: '9px 12px', marginBottom: 6,
                  background: 'var(--bg-1)', border: 'var(--border)',
                  borderRadius: 'var(--radius-md)',
                }}>
                  <span style={{ width: 18, textAlign: 'center', flex: 'none' }}>
                    {r.media_type === 'movie' ? '🎬' : '📺'}
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600, fontSize: 'var(--text-sm)' }}>{r.title}</div>
                    <div className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {r.series_prefix} · {r.covered_count} {r.media_type === 'movie' ? 'file' : 'eps'}
                    </div>
                  </div>
                  <LangTag value={r.lang_code} size={13} />
                  <button className="btn ghost" onClick={() => revoke(r.series_prefix)}
                    style={{ color: 'var(--error-500)', flex: 'none' }}>
                    Revoke
                  </button>
                </div>
              ))}
            </div>
          ))}
        </div>
        <div style={{ flex: 'none', width: 20, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1, paddingTop: 2 }}>
          {Array.from({ length: 26 }, (_, i) => String.fromCharCode(65 + i)).map((L) => (
            <span key={L}
              role={active.has(L) ? 'button' : undefined}
              tabIndex={active.has(L) ? 0 : undefined}
              onClick={active.has(L) ? () => jumpTo(L) : undefined}
              onKeyDown={active.has(L) ? (e) => { if (e.key === 'Enter') jumpTo(L); } : undefined}
              aria-label={active.has(L) ? `Jump to ${L}` : undefined}
              style={{
                fontSize: 10, lineHeight: 1.25,
                color: active.has(L) ? 'var(--violet-500)' : 'var(--fg-3)',
                fontWeight: active.has(L) ? 600 : 400,
                cursor: active.has(L) ? 'pointer' : 'default',
              }}>{L}</span>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─── Page wrapper ────────────────────────────────────────────────
export function SettingsPage() {
  const { data: health, loading, error, refetch } = useLiveHealth();
  const rail = useMemo(() => buildRailItems(health), [health]);
  const instancesHealth = useInstancesHealth();

  const [selectedId, setSelectedId] = useState(null);
  const [view, setView] = useState('integration'); // integration|system|telemetry|updates|providers

  // Default-select the first available integration once we have data.
  useEffect(() => {
    if (!selectedId && rail.length > 0) setSelectedId(rail[0].id);
  }, [rail, selectedId]);

  // #205 fix: honor URL hash on first mount so links like /settings#providers,
  // /settings#telemetry, /settings#system, /settings#updates land directly on
  // the named view rather than dropping the user on the default integration.
  useEffect(() => {
    const hash = (window.location.hash || '').replace(/^#/, '').toLowerCase();
    if (['providers', 'instances', 'libraries', 'telemetry', 'system', 'updates', 'lang-rules', 'subgen-tuning'].includes(hash)) {
      setView(hash);
    }
    // #207: 'integrations' lands on the summary tile grid rather than
    // dumping the user onto Bazarr (the first rail item). From the
    // summary they can click into any integration's detail panel.
    if (hash === 'integrations') {
      setView('integrations-summary');
    }
    // Also support /settings#integration:<name> for direct integration deep-links.
    if (hash.startsWith('integration:')) {
      const id = hash.split(':')[1];
      if (id) { setSelectedId(id); setView('integration'); }
    }
  }, []);

  const selected = rail.find((r) => r.id === selectedId);

  const breadcrumb = view === 'integration' && selected
    ? ['Settings', 'Integrations', selected.name.toLowerCase()]
    : view === 'integrations-summary' ? ['Settings', 'Integrations']
    : view === 'system' ? ['Settings', 'System actions']
    : view === 'telemetry' ? ['Settings', 'Telemetry']
    : view === 'updates' ? ['Settings', 'Updates']
    : view === 'providers' ? ['Settings', 'Providers']
    : view === 'instances' ? ['Settings', 'Instances']
    : view === 'libraries' ? ['Settings', 'Libraries']
    : view === 'lang-rules' ? ['Settings', 'Language rules']
    : view === 'subgen-tuning' ? ['Settings', 'Subgen tuning']
    : ['Settings'];

  const heading = view === 'integration' && selected ? selected.name
    : view === 'integrations-summary' ? 'Integrations'
    : view === 'system' ? 'System actions'
    : view === 'telemetry' ? 'Telemetry'
    : view === 'updates' ? 'Updates'
    : view === 'providers' ? 'Provider leaderboard'
    : view === 'instances' ? 'Instances'
    : view === 'libraries' ? 'Libraries'
    : view === 'lang-rules' ? 'Language rules'
    : view === 'subgen-tuning' ? 'Subgen tuning'
    : 'Settings';

  const subhead = view === 'integration' && selected ? 'Live status from the integrations health probe.'
    : view === 'integrations-summary' ? 'Live status across every connected service. Click a tile to drill in.'
    : view === 'system' ? 'One-shot actions against the running subarr.'
    : view === 'telemetry' ? 'Exactly what subarr sends and how to opt in/out.'
    : view === 'updates' ? 'Per-product version checks against GitHub releases.'
    : view === 'providers' ? 'Bazarr provider success rates from your download history.'
    : view === 'instances' ? 'Connect more than one Sonarr/Radarr/Bazarr stack. The default instance comes from your env config; add others here and bind libraries to them under Libraries.'
    : view === 'libraries' ? 'Media locations subarr walks. Each library maps a filesystem root to its subgen and *arr path prefixes; the default comes from SUBARR_MEDIA_ROOT.'
    : view === 'lang-rules' ? 'Declared audio languages for whole shows and movies. New downloads inherit automatically; a per-file correction always overrides.'
    : view === 'subgen-tuning' ? 'Hardware-matched Whisper model, device and compute type.'
    : '';

  return (
    <div className="app-body" style={{ position: 'relative' }}>
      <SettingsRail
        items={rail}
        selectedId={selectedId}
        onSelect={(id) => { setSelectedId(id); setView('integration'); }}
        systemActive={view === 'system'} onSelectSystem={() => setView('system')}
        telemetryActive={view === 'telemetry'} onSelectTelemetry={() => setView('telemetry')}
        updatesActive={view === 'updates'} onSelectUpdates={() => setView('updates')}
        providersActive={view === 'providers'} onSelectProviders={() => setView('providers')}
        langRulesActive={view === 'lang-rules'} onSelectLangRules={() => setView('lang-rules')}
        subgenTuningActive={view === 'subgen-tuning'} onSelectSubgenTuning={() => setView('subgen-tuning')}
        librariesActive={view === 'libraries'} onSelectLibraries={() => setView('libraries')}
        instancesActive={view === 'instances'} onSelectInstances={() => setView('instances')}
      />
      <main className="main-canvas" style={{ display: 'flex', flexDirection: 'column', minWidth: 0, paddingBottom: 0 }}>
        <div style={{ flex: 1, padding: '22px 26px 24px', overflow: 'auto' }}>
          {/* Page header */}
          <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: 18, maxWidth: 820 }}>
            <div>
              <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)', marginBottom: 4, display: 'flex', gap: 8, alignItems: 'center' }}>
                {breadcrumb.map((b, i) => (
                  <React.Fragment key={i}>
                    {i > 0 && <span>/</span>}
                    <span style={{ color: i === breadcrumb.length - 1 ? 'var(--fg-1)' : 'var(--fg-3)' }} className={i === breadcrumb.length - 1 ? 'mono' : ''}>{b}</span>
                  </React.Fragment>
                ))}
              </div>
              <h1 style={{ margin: 0, fontSize: 'var(--text-h1)', lineHeight: 'var(--lh-h1)', fontWeight: 600, letterSpacing: '-0.005em' }}>{heading}</h1>
              <div style={{ marginTop: 4, fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>{subhead}</div>
            </div>
          </div>

          {error && !health && (
            <div style={{ padding: 20, color: 'var(--error-500)' }}>
              Couldn't load integrations: {String(error.message || error)}
              <div style={{ marginTop: 12 }}><button className="btn" onClick={() => refetch()}>Retry</button></div>
            </div>
          )}
          {loading && !health && (
            <div style={{ padding: 20, color: 'var(--fg-2)' }}>Loading integrations…</div>
          )}

          {view === 'integrations-summary' && health && (
            <>
              <SettingsWelcomeCard />
              <SettingsStatusRow rail={rail} onView={setView} />
              <IntegrationsSummaryPanel
                rail={rail}
                instancesHealth={instancesHealth}
                onSelect={(id) => { setSelectedId(id); setView('integration'); }}
              />
            </>
          )}
          {view === 'integration' && health && <IntegrationPanel rail={selected} refetchHealth={refetch} />}
          {view === 'system' && <SystemPanel />}
          {view === 'telemetry' && <TelemetryPanel />}
          {view === 'updates' && <UpdatesPanel />}
          {view === 'providers' && <ProvidersPanel />}
          {view === 'instances' && <InstancesEditor />}
          {view === 'libraries' && (
            <div style={{ maxWidth: 820 }}>
              <LibrariesEditor showDetected={true} />
            </div>
          )}
          {view === 'lang-rules' && <LangRulesPanel />}
          {view === 'subgen-tuning' && <SubgenSetupFlow />}
        </div>
      </main>
    </div>
  );
}
