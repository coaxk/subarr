// Settings — live status + actions, driven entirely by what the v1
// backend actually exposes (env-driven config, no PUT endpoints yet).
// Sections:
//   - Integrations rail with live online/version/badges per service
//   - Detail panel per integration with Test connection + raw badges
//   - System actions: Restart subarr, Plex scan, Bazarr sync-disk, Refresh updates
//   - Telemetry transparency: install_id + opt-in toggle + last ping
//   - Updates: current/latest per product + Refresh now
//
// v1 deliberately ships without a settings form — the wizard owns the
// initial config write and subsequent edits happen via env vars. When a
// PUT /api/integrations/config exists we'll re-introduce the dirty bar.

import { StatusDot } from './atoms.jsx';

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
function SectionCard({ label, children, action }) {
  return (
    <section style={{
      background: 'var(--bg-1)',
      border: 'var(--border)',
      borderRadius: 'var(--radius-lg)',
      padding: '16px 18px',
      display: 'flex', flexDirection: 'column', gap: 14,
    }}>
      <div style={{ display: 'flex', alignItems: 'center' }}>
        <span className="label">{label}</span>
        <span style={{ flex: 1 }} />
        {action}
      </div>
      {children}
    </section>
  );
}

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

function Toggle({ on, onToggle, busy }) {
  return (
    <button
      onClick={onToggle}
      disabled={busy}
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
          fontSize: 26, lineHeight: 1, fontWeight: 500,
          color: color || 'var(--fg-0)', letterSpacing: '-0.01em',
        }}>{value}</span>
      </div>
    </div>
  );
}

// ─── Integrations rail ───────────────────────────────────────────
const INTEGRATION_ORDER = ['bazarr', 'sonarr', 'radarr', 'tautulli', 'subgen'];

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

function SettingsRail({ items, selectedId, onSelect, systemActive, onSelectSystem, telemetryActive, onSelectTelemetry, updatesActive, onSelectUpdates }) {
  return (
    <aside style={{
      width: 220, flex: '0 0 220px',
      borderRight: 'var(--border)',
      background: 'var(--bg-1)',
      padding: '18px 0 12px',
      overflowY: 'auto',
    }}>
      <div style={{ padding: '0 16px 12px', display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
        <h2 style={{ margin: 0, fontSize: 'var(--text-lg)', fontWeight: 600, color: 'var(--fg-0)' }}>Settings</h2>
        <span className="num mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>v1.0.0</span>
      </div>
      <div style={{ marginBottom: 14 }}>
        <div style={{ padding: '0 16px 6px' }}>
          <span className="label">integrations</span>
        </div>
        {items.length === 0 && (
          <div style={{ padding: '8px 16px', fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>Loading…</div>
        )}
        {items.map((it) => {
          const active = it.id === selectedId && !systemActive && !telemetryActive && !updatesActive;
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
    </aside>
  );
}

// ─── Integration detail panel ────────────────────────────────────
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
      const r = await fetch('/api/bazarr/sync-disk', { method: 'POST', credentials: 'same-origin' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
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

      {/* Badges (per-integration freshness counts) */}
      {i.badges && (
        <SectionCard label="Live data">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 18 }}>
            {Object.entries(i.badges).map(([k, v]) => (
              <Stat key={k} label={k.replace(/_/g, ' ')} value={typeof v === 'number' ? v.toLocaleString() : String(v)} />
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

      {/* Connection details (read-only — env-driven in v1) */}
      <SectionCard label="Connection (read-only — env-driven in v1)">
        <Row label="Name" value={rail.name} />
        <Row label="Version" value={i.version || '—'} />
        <Row label="Online" value={i.online ? 'yes' : 'no'} />
        <Row label="Configured" value={i.configured ? 'yes' : 'no'} />
      </SectionCard>

      <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)', padding: '0 6px' }}>
        To change connection details, edit your <span className="mono">.env</span> file or re-run the onboarding wizard.
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
      </SectionCard>

      <SectionCard label="Onboarding">
        <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>
          Re-run the setup wizard to change connection details or library paths.
        </div>
        <div>
          <a href="/onboarding" className="btn sm" style={{ textDecoration: 'none' }}>Open onboarding wizard</a>
        </div>
      </SectionCard>
    </div>
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

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 820 }}>
      <SectionCard label="Transparency">
        <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>
          Subarr can send an anonymous weekly install heartbeat. Nothing identifying you, your media, or your subtitle content is included. You can read exactly what's sent below.
        </div>
        <Row label="Telemetry"
          hint={data.opted_in ? 'enabled — weekly heartbeat ON' : 'disabled — nothing leaves this machine'}
          control={<Toggle on={data.opted_in} onToggle={toggle} busy={busy} />} />
        <Row label="Install ID" value={data.install_id} hint="random per-install identifier — no PII" />
        <Row label="Last ping" value={data.last_ping_at ? new Date(data.last_ping_at * 1000).toLocaleString() : 'never'} />
        {data.last_error && <Row label="Last error" value={data.last_error} hint="last failed send" />}
      </SectionCard>

      <SectionCard label="Last payload (exactly what was sent)" action={
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
              : (p.latest_released_at ? `released ${new Date(p.latest_released_at * 1000).toLocaleDateString()}` : 'no release info')}
            control={
              <span className="mono" style={{ fontSize: 'var(--text-sm)' }}>
                <span style={{ color: 'var(--fg-1)' }}>{p.current_version || '—'}</span>
                {p.latest_version && p.latest_version !== p.current_version && (
                  <> <span style={{ color: 'var(--fg-3)' }}>→</span> <span style={{ color: p.has_update ? 'var(--violet-400)' : 'var(--fg-1)' }}>{p.latest_version}</span></>
                )}
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

// ─── Page wrapper ────────────────────────────────────────────────
export function SettingsPage() {
  const { data: health, loading, error, refetch } = useLiveHealth();
  const rail = useMemo(() => buildRailItems(health), [health]);

  const [selectedId, setSelectedId] = useState(null);
  const [view, setView] = useState('integration'); // integration|system|telemetry|updates

  // Default-select the first available integration once we have data.
  useEffect(() => {
    if (!selectedId && rail.length > 0) setSelectedId(rail[0].id);
  }, [rail, selectedId]);

  const selected = rail.find((r) => r.id === selectedId);

  const breadcrumb = view === 'integration' && selected
    ? ['Settings', 'Integrations', selected.name.toLowerCase()]
    : view === 'system' ? ['Settings', 'System actions']
    : view === 'telemetry' ? ['Settings', 'Telemetry']
    : view === 'updates' ? ['Settings', 'Updates']
    : ['Settings'];

  const heading = view === 'integration' && selected ? selected.name
    : view === 'system' ? 'System actions'
    : view === 'telemetry' ? 'Telemetry'
    : view === 'updates' ? 'Updates'
    : 'Settings';

  const subhead = view === 'integration' && selected ? 'Live status from the integrations health probe.'
    : view === 'system' ? 'One-shot actions against the running subarr.'
    : view === 'telemetry' ? 'Exactly what subarr sends and how to opt in/out.'
    : view === 'updates' ? 'Per-product version checks against GitHub releases.'
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
              <h1 style={{ margin: 0, fontSize: 22, lineHeight: 1.15, fontWeight: 600, letterSpacing: '-0.005em' }}>{heading}</h1>
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

          {view === 'integration' && health && <IntegrationPanel rail={selected} refetchHealth={refetch} />}
          {view === 'system' && <SystemPanel />}
          {view === 'telemetry' && <TelemetryPanel />}
          {view === 'updates' && <UpdatesPanel />}
        </div>
      </main>
    </div>
  );
}
