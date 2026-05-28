// Settings — grouped left rail + Bazarr detail panel + dirty action bar.

import { StatusDot } from './atoms.jsx';

const SETTINGS_GROUPS = [
  {
    label: 'integrations',
    items: [
      { id: 'bazarr',   name: 'Bazarr',   status: 'ok',    meta: '12s',  active: true },
      { id: 'sonarr',   name: 'Sonarr',   status: 'ok',    meta: '62s' },
      { id: 'radarr',   name: 'Radarr',   status: 'ok',    meta: '71s' },
      { id: 'tautulli', name: 'Tautulli', status: 'ok',    meta: '23s' },
      { id: 'subgen',   name: 'subgen',   status: 'error', meta: '14m' },
    ],
  },
  {
    label: 'scheduler',
    items: [
      { id: 'walk',         name: 'Walk schedule' },
      { id: 'backpressure', name: 'Backpressure' },
      { id: 'quiet',        name: 'Quiet hours' },
    ],
  },
  {
    label: 'paths',
    items: [
      { id: 'roots',    name: 'Library roots' },
      { id: 'mapping',  name: 'Path mapping' },
      { id: 'excludes', name: 'Excludes' },
    ],
  },
  {
    label: 'telemetry',
    items: [
      { id: 'logs',      name: 'Logs' },
      { id: 'metrics',   name: 'Metrics' },
      { id: 'ledger',    name: 'Provenance ledger' },
    ],
  },
  {
    label: 'advanced',
    items: [
      { id: 'tokens', name: 'API tokens' },
      { id: 'db',     name: 'Database' },
      { id: 'reset',  name: 'Reset', destructive: true },
    ],
  },
];

// ─── Grouped rail ────────────────────────────────────────────────
function SettingsRail() {
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
      {SETTINGS_GROUPS.map((g, gi) => (
        <div key={g.label} style={{ marginBottom: 14, paddingTop: gi === 0 ? 0 : 4 }}>
          <div style={{ padding: '0 16px 6px' }}>
            <span className="label">{g.label}</span>
          </div>
          {g.items.map(it => (
            <a key={it.id} href="#" style={{
              display: 'flex', alignItems: 'center', gap: 8,
              height: 28,
              padding: '0 16px 0 14px',
              borderLeft: `2px solid ${it.active ? 'var(--violet-500)' : 'transparent'}`,
              background: it.active ? 'rgba(139,92,246,0.08)' : 'transparent',
              color: it.active ? 'var(--fg-0)' : it.destructive ? 'var(--error-500)' : 'var(--fg-1)',
              fontSize: 'var(--text-base)',
              fontWeight: it.active ? 600 : 500,
              textDecoration: 'none',
              transition: 'background var(--dur-fast)',
            }}
            onMouseEnter={e => !it.active && (e.currentTarget.style.background = 'var(--bg-2)')}
            onMouseLeave={e => !it.active && (e.currentTarget.style.background = 'transparent')}>
              {it.status && <StatusDot kind={it.status} />}
              <span style={{ flex: 1 }}>{it.name}</span>
              {it.meta && (
                <span className="mono num" style={{ fontSize: 'var(--text-2xs)', color: it.active ? 'var(--fg-2)' : 'var(--fg-3)' }}>{it.meta}</span>
              )}
            </a>
          ))}
        </div>
      ))}
    </aside>
  );
}

// ─── Form bits ───────────────────────────────────────────────────
function SetField({ label, value, hint, dirty, suffix, mono = true, w }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 5, width: w }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
        <span style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-1)', fontWeight: 500 }}>{label}</span>
        {hint && <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>· {hint}</span>}
        {dirty && (
          <span className="num mono" style={{
            fontSize: 'var(--text-2xs)',
            color: 'var(--warn-500)',
            marginLeft: 'auto',
            letterSpacing: '0.04em',
            textTransform: 'uppercase',
          }}>edited</span>
        )}
      </div>
      <div style={{
        display: 'flex', alignItems: 'center',
        height: 32, padding: '0 12px',
        background: 'var(--bg-2)',
        border: `1px solid ${dirty ? 'var(--warn-500)' : 'var(--bg-4)'}`,
        borderRadius: 'var(--radius-md)',
      }}>
        <span className={mono ? 'mono' : ''} style={{ flex: 1, fontSize: 'var(--text-md)', color: 'var(--fg-0)' }}>{value}</span>
        {suffix}
      </div>
    </label>
  );
}

function Toggle({ on, dirty }) {
  return (
    <span style={{
      display: 'inline-block', width: 30, height: 17,
      borderRadius: 99,
      background: on ? (dirty ? 'var(--warn-500)' : 'var(--violet-500)') : 'var(--bg-4)',
      position: 'relative',
      transition: 'background var(--dur-fast)',
    }}>
      <span style={{
        position: 'absolute',
        top: 2,
        left: on ? 15 : 2,
        width: 13, height: 13,
        background: '#fff',
        borderRadius: '50%',
        transition: 'left var(--dur-fast) var(--ease-out)',
      }} />
    </span>
  );
}

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

function Row({ label, value, dirty, hint, control }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, padding: '6px 0', borderBottom: '1px solid var(--bg-3)' }}>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 'var(--text-md)', color: 'var(--fg-0)' }}>{label}</div>
        {hint && <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)', marginTop: 2 }}>{hint}</div>}
      </div>
      {dirty && <span className="num mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--warn-500)', textTransform: 'uppercase' }}>edited</span>}
      {control || <span className="mono" style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-1)' }}>{value}</span>}
    </div>
  );
}

// ─── Bazarr panel ────────────────────────────────────────────────
function BazarrPanel() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 820 }}>
      {/* Top status strip */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 16,
        padding: '14px 18px',
        background: 'rgba(52,211,153,0.04)',
        border: '1px solid rgba(52,211,153,0.28)',
        borderRadius: 'var(--radius-lg)',
      }}>
        <StatusDot kind="ok" size="lg" pulse />
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 'var(--text-md)', color: 'var(--fg-0)', fontWeight: 600 }}>
            Connected · Bazarr <span className="mono">1.5.6</span>
          </div>
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)', marginTop: 2 }}>
            last ping <span className="mono">12s ago</span> · 658 wanted · 12 providers · round-trip <span className="mono">84ms</span>
          </div>
        </div>
        <button className="btn sm">↻ Test connection</button>
      </div>

      {/* Connection */}
      <SectionCard label="Connection">
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
          <SetField label="URL" hint="reachable from this container" value="http://bazarr:6767" />
          <SetField label="API key" hint="from Bazarr Settings → General" value="••••••••••••••••a4f2"
            suffix={<button style={{
              fontSize: 'var(--text-xs)',
              color: 'var(--fg-2)',
              padding: '2px 6px',
              border: '1px solid var(--bg-4)',
              borderRadius: 2,
              background: 'var(--bg-3)',
              marginLeft: 6,
            }}>Reveal</button>}
          />
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn">Test connection</button>
          <span style={{ flex: 1 }} />
          <button className="btn ghost" style={{ color: 'var(--error-500)' }}>Disconnect</button>
        </div>
      </SectionCard>

      {/* Behaviour */}
      <SectionCard label="Behaviour">
        <Row label="Poll wanted lists" hint="how often subarr reads /api/episodes/wanted" value="every 1h" />
        <Row label="Write back as score" hint="score sent to Bazarr alongside the subtitle" value="9 (subarr-verified)" dirty />
        <Row label="Honour Bazarr language profile" hint="when off, subarr applies its own allow/deny lists" control={<Toggle on />} />
        <Row label="Submit kwargs trace" hint="forwards the subgen kwargs to Bazarr provenance fields" control={<Toggle on={false} />} />
      </SectionCard>

      {/* Schedule */}
      <SectionCard label="Polling schedule">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 14 }}>
          <SetField label="cron" hint="standard 5-field" value="0 * * * *" />
          <SetField label="quiet hours" hint="local time" value="02:00–06:00" />
          <SetField label="jitter" hint="random delay added per run" value="±90s" />
        </div>
      </SectionCard>

      {/* Last 7 days */}
      <SectionCard label="Last 7 days" action={
        <a href="#" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)', textDecoration: 'none' }}>View provenance →</a>
      }>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 18 }}>
          <Stat label="polled"        value="168" delta="+24" />
          <Stat label="written-back"  value="412" delta="+39" />
          <Stat label="errors"        value="3"   color="var(--error-500)" />
          <Stat label="avg round-trip" value="84ms" />
        </div>
      </SectionCard>
    </div>
  );
}

function Stat({ label, value, delta, color }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <span className="label">{label}</span>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span className="display num" style={{
          fontSize: 26, lineHeight: 1,
          fontWeight: 500,
          color: color || 'var(--fg-0)',
          letterSpacing: '-0.01em',
        }}>{value}</span>
        {delta && (
          <span className="num mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--success-500)' }}>↑ {delta}</span>
        )}
      </div>
    </div>
  );
}

// ─── Dirty action bar ────────────────────────────────────────────
function DirtyBar({ visible }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 14,
      padding: '12px 22px',
      background: 'var(--bg-1)',
      borderTop: '1px solid var(--violet-500)',
      boxShadow: '0 -2px 18px rgba(0,0,0,0.35)',
      transform: visible ? 'translateY(0)' : 'translateY(100%)',
      transition: 'transform var(--dur-base) var(--ease-out)',
    }}>
      <StatusDot kind="violet" pulse />
      <span style={{ fontSize: 'var(--text-md)', color: 'var(--fg-0)', fontWeight: 500 }}>
        2 unsaved changes
      </span>
      <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
        · write-back score <span className="mono">8 → 9</span>
        · poll interval <span className="mono">2h → 1h</span>
      </span>
      <span style={{ flex: 1 }} />
      <button className="btn ghost">Discard</button>
      <button className="btn primary">Save changes</button>
    </div>
  );
}

// ─── Page wrapper ────────────────────────────────────────────────
export function SettingsPage() {
  return (
    <div className="app-body" style={{ position: 'relative' }}>
      <SettingsRail />
      <main className="main-canvas" style={{ display: 'flex', flexDirection: 'column', minWidth: 0, paddingBottom: 0 }}>
        <div style={{ flex: 1, padding: '22px 26px 24px', overflow: 'auto' }}>
          {/* Page header */}
          <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: 18, maxWidth: 820 }}>
            <div>
              <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)', marginBottom: 4, display: 'flex', gap: 8, alignItems: 'center' }}>
                <span>Settings</span><span>/</span><span style={{ color: 'var(--fg-2)' }}>Integrations</span><span>/</span><span className="mono" style={{ color: 'var(--fg-1)' }}>bazarr</span>
              </div>
              <h1 style={{ margin: 0, fontSize: 22, lineHeight: 1.15, fontWeight: 600, letterSpacing: '-0.005em' }}>Bazarr</h1>
              <div style={{ marginTop: 4, fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>
                Subarr reads wanted lists and writes completed subtitles back through Bazarr.
              </div>
            </div>
            <div style={{ display: 'flex', gap: 10 }}>
              <button className="btn ghost">View logs</button>
              <button className="btn">Edit YAML</button>
            </div>
          </div>

          <BazarrPanel />
        </div>
        <DirtyBar visible />
      </main>
    </div>
  );
}

