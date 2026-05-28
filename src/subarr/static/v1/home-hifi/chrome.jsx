// Top nav (Nav variant B) + contextual sub-rail.

// hrefs are subarr server routes — kept clean (no Subarr+...html design-asset
// filenames). The route layer in app.py maps each path to its rendered
// static HTML under /static/v1/.
const TOP_SECTIONS = [
  { id: 'overview',   label: 'Overview',   href: '/home' },
  { id: 'operations', label: 'Operations', href: '/coverage' },
  { id: 'library',    label: 'Library',    href: '#' },
  { id: 'config',     label: 'Config',     href: '/settings' },
];

const SUB_RAIL_BY_SECTION = {
  overview: [
    { id: 'dashboard', label: 'Dashboard', count: null,    href: '/home' },
    { id: 'health',    label: 'Health',    count: '5/5',   href: '#' },
    { id: 'schedule',  label: 'Schedule',  count: '4h 12m',href: '#' },
    { id: 'audit',     label: 'Audit log', count: null,    href: '#' },
  ],
  operations: [
    { id: 'coverage', label: 'Coverage', count: 612,  href: '/coverage' },
    { id: 'activity', label: 'Activity', count: null, href: '/file-modal' },
    { id: 'queue',    label: 'Queue',    count: 6,    href: '#' },
    { id: 'rules',    label: 'Rules',    count: 4,    href: '/rules' },
  ],
  library: [
    { id: 'browse',     label: 'Browse',     count: 12482, href: '#' },
    { id: 'languages',  label: 'Languages',  count: 7,     href: '#' },
    { id: 'provenance', label: 'Provenance', count: null,  href: '#' },
  ],
  config: [
    { id: 'integrations', label: 'Integrations', count: '4/5', href: '/settings' },
    { id: 'scheduler',    label: 'Scheduler',    count: null,  href: '#' },
    { id: 'paths',        label: 'Paths',        count: 3,     href: '#' },
    { id: 'telemetry',    label: 'Telemetry',    count: null,  href: '#' },
    { id: 'advanced',     label: 'Advanced',     count: null,  href: '#' },
  ],
};

// ─── Top bar ─────────────────────────────────────────────────────
function TopBar({ section = 'overview', healthKind = 'warn', healthLabel = '4/5 healthy' }) {
  return (
    <header style={{
      height: 48,
      display: 'flex',
      alignItems: 'center',
      padding: '0 var(--space-5)',
      borderBottom: 'var(--border)',
      background: 'var(--bg-1)',
      flex: '0 0 auto',
    }}>
      <a href="/home" style={{ marginRight: 28, textDecoration: 'none', color: 'inherit' }}>
        <Wordmark size={17} />
      </a>

      <nav style={{ display: 'flex', alignItems: 'stretch', height: '100%' }}>
        {TOP_SECTIONS.map(s => {
          const active = s.id === section;
          return (
            <a key={s.id} href={s.href} style={{
              position: 'relative',
              padding: '0 16px',
              height: '100%',
              display: 'flex', alignItems: 'center',
              fontSize: 'var(--text-md)',
              fontWeight: active ? 600 : 500,
              color: active ? 'var(--fg-0)' : 'var(--fg-2)',
              textDecoration: 'none',
              transition: 'color var(--dur-fast) var(--ease-out)',
            }}
            onMouseEnter={e => !active && (e.currentTarget.style.color = 'var(--fg-0)')}
            onMouseLeave={e => !active && (e.currentTarget.style.color = 'var(--fg-2)')}>
              {s.label}
              {active && <span style={{
                position: 'absolute',
                left: 16, right: 16, bottom: -1, height: 2,
                background: 'var(--violet-500)',
                borderRadius: '2px 2px 0 0',
              }} />}
            </a>
          );
        })}
      </nav>

      <div style={{ flex: 1 }} />

      {/* Global search */}
      <button style={{
        display: 'flex', alignItems: 'center', gap: 8,
        height: 28,
        padding: '0 10px 0 10px',
        borderRadius: 'var(--radius-md)',
        background: 'var(--bg-2)',
        border: 'var(--border)',
        color: 'var(--fg-2)',
        fontSize: 'var(--text-sm)',
        minWidth: 240,
      }}>
        <Glyph char="⌕" size={14} color="var(--fg-2)" />
        <span style={{ flex: 1, textAlign: 'left' }}>Search files, rules, paths…</span>
        <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)', border: '1px solid var(--bg-4)', padding: '0 4px', borderRadius: 2, lineHeight: '14px' }}>⌘K</span>
      </button>

      <div style={{ width: 16 }} />

      {/* Health blob */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <StatusDot kind={healthKind} size="lg" />
        <span className="num" style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-1)' }}>{healthLabel}</span>
      </div>

      <div style={{ width: 14 }} />
      <span style={{ width: 1, height: 22, background: 'var(--bg-4)' }} />
      <div style={{ width: 14 }} />

      <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }} className="num mono">v1.0.0</span>
    </header>
  );
}

// ─── Contextual sub-rail ─────────────────────────────────────────
function SubRail({ section = 'overview', activeId, footer }) {
  const items = SUB_RAIL_BY_SECTION[section] || [];
  return (
    <aside style={{
      width: 184,
      flex: '0 0 184px',
      borderRight: 'var(--border)',
      background: 'var(--bg-1)',
      padding: '14px 0',
      display: 'flex',
      flexDirection: 'column',
      gap: 1,
    }}>
      <div style={{ padding: '0 16px 6px' }}>
        <span className="label">{section}</span>
      </div>
      {items.map(it => (
        <RailItem key={it.id} item={{ ...it, active: it.id === activeId }} />
      ))}
      <div style={{ flex: 1 }} />
      {footer ?? (
        <div style={{ padding: '10px 16px', borderTop: '1px solid var(--bg-3)', display: 'flex', alignItems: 'center', gap: 8 }}>
          <StatusDot kind="ok" />
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>walker idle</span>
          <span style={{ flex: 1 }} />
          <span className="num mono" style={{ fontSize: 10, color: 'var(--fg-3)' }}>4h12m</span>
        </div>
      )}
    </aside>
  );
}

function RailItem({ item }) {
  return (
    <a href={item.href || '#'} style={{
      position: 'relative',
      display: 'flex', alignItems: 'center', gap: 8,
      padding: '6px 16px 6px 14px',
      height: 30,
      borderLeft: `2px solid ${item.active ? 'var(--violet-500)' : 'transparent'}`,
      background: item.active ? 'rgba(139,92,246,0.08)' : 'transparent',
      color: item.active ? 'var(--fg-0)' : 'var(--fg-1)',
      fontSize: 'var(--text-base)',
      fontWeight: item.active ? 600 : 500,
      width: '100%',
      textDecoration: 'none',
      transition: 'background var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out)',
    }}
    onMouseEnter={e => !item.active && (e.currentTarget.style.background = 'var(--bg-2)')}
    onMouseLeave={e => !item.active && (e.currentTarget.style.background = 'transparent')}>
      <span style={{ flex: 1, textAlign: 'left' }}>{item.label}</span>
      {item.count != null && (
        <span className="num mono" style={{
          fontSize: 'var(--text-xs)',
          color: item.active ? 'var(--fg-1)' : 'var(--fg-3)',
        }}>{item.count}</span>
      )}
    </a>
  );
}

Object.assign(window, { TopBar, SubRail });
