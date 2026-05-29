// Top nav (Nav variant B) + contextual sub-rail.
//
// All hrefs point at real subarr routes. Subrail counts hydrate from
// /api/home/dashboard, /api/integrations/health, /api/queue, and
// /api/schedule, polled every 10s. The old hardcoded counts (612, 6,
// 4, etc.) were design preview only — they were the same lie the
// frontend-backend wiring audit flagged.

import { Wordmark, Glyph, StatusDot } from './atoms.jsx';

const { useState, useEffect, useCallback } = React;

// hrefs are subarr server routes; the route layer in app.py maps each
// path to its rendered static HTML under /static/v1/.
const TOP_SECTIONS = [
  { id: 'overview',   label: 'Overview',   href: '/home' },
  { id: 'operations', label: 'Operations', href: '/coverage' },
  { id: 'library',    label: 'Library',    href: '/library' },
  { id: 'config',     label: 'Config',     href: '/settings' },
];

// ─── Live counts hook ────────────────────────────────────────────
// Pulls from the four "small" endpoints (all cached or cheap) every
// 10s so SubRail badges reflect reality. Returns an object keyed by
// subrail item id; missing keys render as no badge.
export function useLiveChromeCounts(intervalMs = 10000) {
  const [counts, setCounts] = useState({});

  const tick = useCallback(async () => {
    try {
      const [dash, health, queue, schedule] = await Promise.all([
        fetch('/api/home/dashboard', { credentials: 'same-origin' }).then(r => r.ok ? r.json() : null).catch(() => null),
        fetch('/api/integrations/health', { credentials: 'same-origin' }).then(r => r.ok ? r.json() : null).catch(() => null),
        fetch('/api/queue', { credentials: 'same-origin' }).then(r => r.ok ? r.json() : null).catch(() => null),
        fetch('/api/schedule', { credentials: 'same-origin' }).then(r => r.ok ? r.json() : null).catch(() => null),
      ]);

      const next = {};

      // Operations / Coverage — count of open gaps from the dashboard
      // stages block when present, else null (better than the old 612).
      if (dash?.stages) {
        const wanted = dash.stages.find((s) => s.id === 'wanted' || s.id === 'bazarr-wanted');
        if (wanted) next.coverage = wanted.count;
      }

      // Operations / Queue — sum of queued + processing
      if (queue) {
        const total = (queue.queued_count || 0) + (queue.processing_count || 0);
        next.queue = total;
      }

      // Operations / Rules — count of enabled schedules from /api/schedule
      if (schedule?.schedules) {
        next.rules = schedule.schedules.filter((s) => s.enabled).length;
      }

      // Overview / Health — N/M online integrations
      if (health) {
        const ints = health.integrations || [];
        const online = ints.filter((i) => i.online).length;
        const total = ints.length + (health.subgen ? 1 : 0);
        const onlineWithSubgen = online + (health.subgen?.reachable ? 1 : 0);
        next.health = `${onlineWithSubgen}/${total}`;
      }

      // Overview / Schedule — countdown to next run
      if (dash?.next_run?.countdown_s != null) {
        const s = dash.next_run.countdown_s;
        if (s < 60) next.schedule = `${s}s`;
        else if (s < 3600) next.schedule = `${Math.floor(s/60)}m`;
        else next.schedule = `${Math.floor(s/3600)}h ${String(Math.floor((s%3600)/60)).padStart(2,'0')}m`;
      } else if (dash?.next_run?.enabled === false) {
        next.schedule = 'off';
      }

      // Config / Integrations — same N/M as health
      if (next.health) next.config_integrations = next.health;

      setCounts(next);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.debug('chrome counts fetch failed:', e);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer = null;
    async function loop() {
      if (cancelled) return;
      await tick();
      if (!cancelled) timer = setTimeout(loop, intervalMs);
    }
    loop();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [tick, intervalMs]);

  return counts;
}

// SubRail items are defined as functions of the live-counts object so
// the badges and labels stay accurate.
function railItems(section, counts) {
  switch (section) {
    case 'overview': return [
      { id: 'dashboard', label: 'Dashboard', count: null,             href: '/home' },
      { id: 'health',    label: 'Health',    count: counts.health,    href: '/settings' },
      { id: 'schedule',  label: 'Schedule',  count: counts.schedule,  href: '/rules' },
      { id: 'audit',     label: 'Audit log', count: null,             href: '/file-modal' },
    ];
    case 'operations': return [
      { id: 'coverage', label: 'Coverage', count: counts.coverage, href: '/coverage' },
      { id: 'queue',    label: 'Queue',    count: counts.queue,    href: '/queue' },
      { id: 'activity', label: 'Activity', count: null,            href: '/file-modal' },
      { id: 'rules',    label: 'Rules',    count: counts.rules,    href: '/rules' },
    ];
    case 'library': return [
      // The Library page renders its own breadcrumbs + tree; the rail
      // links match the only views v1 actually has. No fake Browse /
      // Languages / Provenance triple-link.
      { id: 'browse', label: 'Browse', count: null, href: '/library' },
    ];
    case 'config': return [
      { id: 'integrations', label: 'Integrations', count: counts.config_integrations, href: '/settings' },
      { id: 'scheduler',    label: 'Scheduler',    count: null,                       href: '/rules' },
      { id: 'telemetry',    label: 'Telemetry',    count: null,                       href: '/settings#telemetry' },
      // Paths + Advanced cut — no v1.0 UI exists for either; they are
      // env-driven. Re-add when the corresponding settings views ship.
    ];
    default: return [];
  }
}

// ─── Top bar ─────────────────────────────────────────────────────
export function TopBar({ section = 'overview' }) {
  const counts = useLiveChromeCounts();
  // Derive health from live integrations health.
  const healthLabel = counts.health || '—';
  const healthKind = counts.health
    ? (counts.health.startsWith(counts.health.split('/')[1]) ? 'ok' : 'warn')
    : 'muted';

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

      {/* Global search — disabled in v1.0, full ⌘K palette ships v1.1.
          Keeping the visual placeholder but stripped of hover/click affordances
          would mislead users; instead we hide it entirely until wired. */}

      {/* Health blob — live count of online integrations */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <StatusDot kind={healthKind} size="lg" />
        <span className="num" style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-1)' }}>
          {healthLabel} {healthLabel !== '—' && <span style={{ color: 'var(--fg-3)' }}>healthy</span>}
        </span>
      </div>

      <div style={{ width: 14 }} />
      <span style={{ width: 1, height: 22, background: 'var(--bg-4)' }} />
      <div style={{ width: 14 }} />

      <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }} className="num mono">v1.0.0</span>
    </header>
  );
}

// ─── Contextual sub-rail ─────────────────────────────────────────
export function SubRail({ section = 'overview', activeId, footer }) {
  const counts = useLiveChromeCounts();
  const items = railItems(section, counts);
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
      {footer ?? <RailFooter />}
    </aside>
  );
}

// Footer renders the live walker state pulled from /api/home/dashboard.
function RailFooter() {
  const [data, setData] = useState(null);
  useEffect(() => {
    let cancelled = false; let timer;
    async function tick() {
      try {
        const r = await fetch('/api/home/dashboard', { credentials: 'same-origin' });
        if (r.ok && !cancelled) setData(await r.json());
      } catch {}
      if (!cancelled) timer = setTimeout(tick, 10000);
    }
    tick();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, []);

  const walking = data?.next_run?.running === true;
  const last = data?.next_run?.last_run_at;
  const lastLabel = last ? new Date(last * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '—';
  const countdown = data?.next_run?.countdown_s;
  const countdownLabel = countdown == null ? null
    : countdown < 60 ? `${countdown}s`
    : countdown < 3600 ? `${Math.floor(countdown/60)}m`
    : `${Math.floor(countdown/3600)}h${String(Math.floor((countdown%3600)/60)).padStart(2,'0')}m`;

  return (
    <div style={{ padding: '10px 16px', borderTop: '1px solid var(--bg-3)', display: 'flex', alignItems: 'center', gap: 8 }}>
      <StatusDot kind={walking ? 'info' : 'ok'} pulse={walking} />
      <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
        {walking ? 'walking…' : 'walker idle'}
      </span>
      <span style={{ flex: 1 }} />
      <span className="num mono" style={{ fontSize: 10, color: 'var(--fg-3)' }}>
        {countdownLabel || lastLabel}
      </span>
    </div>
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
