// Home dashboard body — stages, host telemetry, next run + activity.

import { Sparkline, Delta, StatusDot, Glyph, genSpark } from './atoms.jsx';

// Hooks come from the global React (CDN). Under the old in-browser
// Babel pipeline this destructure lived in atoms.jsx and leaked across
// the shared global scope; under the ESM bundle each module needs its
// own.
const { useMemo } = React;

// ─── Live data hook ──────────────────────────────────────────────
// Fetches GET /api/home/dashboard every 5s. Returns the live payload
// or null if the endpoint hasn't responded yet (during first paint or
// when the backend is unreachable). Each section component below
// falls back to the demo constants below when live data is null —
// so previews + dev render fine even without a running backend.

export function useLiveDashboard(intervalMs = 5000) {
  const [data, setData] = React.useState(null);
  React.useEffect(() => {
    let cancelled = false;
    async function tick() {
      try {
        const r = await fetch('/api/home/dashboard', { credentials: 'same-origin' });
        if (!r.ok) return;
        const d = await r.json();
        if (!cancelled) setData(d);
      } catch (e) {
        // Network or server error — keep stale data, retry next tick.
        // eslint-disable-next-line no-console
        console.debug('home/dashboard fetch failed:', e);
      }
    }
    tick();
    const id = setInterval(tick, intervalMs);
    return () => { cancelled = true; clearInterval(id); };
  }, [intervalMs]);
  return data;
}

// ─── Demo data (fallback) ────────────────────────────────────────
const STAGES = [
  {
    id: 'discovered',
    label: 'discovered',
    count: 12482,
    delta: 47,
    spark: genSpark(20, 8, 4),
    top: '/TV/Severance/Season 02/Severance.S02E08.mkv',
    topMeta: '4.2 GB · 58m',
  },
  {
    id: 'probing',
    label: 'probing',
    count: 38,
    delta: 12,
    spark: genSpark(20, 6, 5),
    top: '/Movies/Dune Part Two (2024)/Dune.Part.Two.2160p.mkv',
    topMeta: '20.4 GB · 2h46',
    live: true,
  },
  {
    id: 'wanted',
    label: 'bazarr-wanted',
    count: 217,
    delta: -8,
    spark: genSpark(20, 12, 6),
    top: '/TV/Andor/Season 01/Andor.S01E04.mkv',
    topMeta: 'eng · 8.6',
  },
  {
    id: 'scanning',
    label: 'scanning',
    count: 6,
    delta: 6,
    spark: genSpark(20, 4, 3),
    top: '/Movies/Anora (2024)/Anora.2024.1080p.mkv',
    topMeta: 'subgen · 14s remaining',
    live: true,
  },
  {
    id: 'written',
    label: 'written-back',
    count: 1023,
    delta: 39,
    spark: genSpark(20, 18, 6),
    top: '/TV/Fallout/Season 01/Fallout.S01E03.mkv',
    topMeta: 'eng · 9.1',
    tail: '4 failed',
    tailKind: 'error',
  },
];

const INTEGRATIONS = [
  { name: 'Bazarr',   ver: '1.5.6',  ping: 12,  status: 'ok',    extra: '658 wanted' },
  { name: 'Sonarr',   ver: '4.0.9',  ping: 62,  status: 'ok',    extra: '4 instances' },
  { name: 'Radarr',   ver: '5.8.3',  ping: 71,  status: 'ok',    extra: '2 instances' },
  { name: 'Tautulli', ver: '2.14.3', ping: 23,  status: 'ok',    extra: '142 plays/wk' },
  { name: 'subgen',   ver: '0.9.2',  ping: 866, status: 'error', extra: 'HTTP 502 · 14m ago' },
];

const ACTIVITY = [
  { t: '14:32:08', rel: '1s',  kind: 'written-back', path: '/TV/Severance/Season 02/Severance.S02E07.mkv',         meta: 'eng · 9.4' },
  { t: '14:31:54', rel: '15s', kind: 'scanned',      path: '/Movies/Furiosa (2024)/Furiosa.2024.mkv',              meta: 'subgen · 22s' },
  { t: '14:31:40', rel: '29s', kind: 'queued',       path: '/TV/Shogun/Season 01/Shogun.S01E08.mkv',               meta: 'eng' },
  { t: '14:30:11', rel: '2m',  kind: 'probed',       path: '/TV/Andor/Season 01/Andor.S01E04.mkv',                 meta: '2h12m · 5.1' },
  { t: '14:29:22', rel: '3m',  kind: 'failed',       path: '/Movies/Madame Web (2024)/Madame.Web.2024.mkv',        meta: 'opensubs 429' },
  { t: '14:28:50', rel: '3m',  kind: 'written-back', path: '/TV/Fallout/Season 01/Fallout.S01E03.mkv',             meta: 'eng · 9.1' },
  { t: '14:28:33', rel: '4m',  kind: 'scanned',      path: '/TV/3 Body Problem/Season 01/3 Body Problem.S01E05.mkv', meta: 'subgen · 31s' },
  { t: '14:27:11', rel: '5m',  kind: 'probed',       path: '/TV/Ripley/Season 01/Ripley.S01E03.mkv',               meta: '54m · 5.1' },
  { t: '14:26:48', rel: '5m',  kind: 'queued',       path: '/Movies/Civil War (2024)/Civil.War.2024.mkv',          meta: 'eng' },
  { t: '14:25:02', rel: '7m',  kind: 'written-back', path: '/TV/Ripley/Season 01/Ripley.S01E02.mkv',               meta: 'eng,ita · 8.4' },
];

const KIND_STYLE = {
  'discovered':   { dot: 'muted',  fg: 'var(--fg-2)' },
  'probed':       { dot: 'info',   fg: 'var(--fg-1)' },
  'queued':       { dot: 'muted',  fg: 'var(--fg-1)' },
  'wanted':       { dot: 'muted',  fg: 'var(--fg-1)' },
  'scanned':      { dot: 'violet', fg: 'var(--fg-1)' },
  'written-back': { dot: 'ok',     fg: 'var(--fg-0)' },
  'failed':       { dot: 'error',  fg: 'var(--error-500)' },
};

// ─── Page header ─────────────────────────────────────────────────
export function PageHeader({ now }) {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'flex-end',
      justifyContent: 'space-between',
      marginBottom: 18,
    }}>
      <div>
        <h1 style={{
          margin: 0,
          fontSize: 22, lineHeight: 1.15,
          fontWeight: 600,
          letterSpacing: '-0.005em',
        }}>Dashboard</h1>
        <div style={{ marginTop: 4, display: 'flex', alignItems: 'center', gap: 10, color: 'var(--fg-2)', fontSize: 'var(--text-sm)' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <StatusDot kind="ok" pulse /> live
          </span>
          <span style={{ width: 1, height: 12, background: 'var(--bg-4)' }} />
          <span>last updated</span>
          <span className="mono num" style={{ color: 'var(--fg-1)' }}>{now}</span>
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{
          display: 'flex',
          border: 'var(--border)',
          borderRadius: 'var(--radius-md)',
          overflow: 'hidden',
          background: 'var(--bg-1)',
        }}>
          {['1h', '24h', '7d'].map((r, i) => (
            <button key={r} style={{
              padding: '0 12px', height: 28,
              fontSize: 'var(--text-sm)',
              color: i === 1 ? 'var(--fg-0)' : 'var(--fg-2)',
              fontWeight: i === 1 ? 600 : 500,
              background: i === 1 ? 'var(--bg-3)' : 'transparent',
              borderLeft: i > 0 ? '1px solid var(--bg-4)' : 'none',
            }}>{r}</button>
          ))}
        </div>
        <button className="btn">Edit rule</button>
        <button className="btn primary">Run now</button>
      </div>
    </div>
  );
}

// ─── Stage tile ──────────────────────────────────────────────────
function StageTile({ s }) {
  return (
    <a className="stage-tile" style={{
      flex: 1, minWidth: 0,
      background: 'var(--bg-1)',
      border: 'var(--border)',
      borderRadius: 'var(--radius-lg)',
      padding: '12px 14px 12px',
      display: 'flex', flexDirection: 'column',
      gap: 8,
      transition: 'background var(--dur-fast) var(--ease-out), border-color var(--dur-fast) var(--ease-out)',
      textDecoration: 'none',
      color: 'inherit',
      cursor: 'pointer',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span className="label">{s.label}</span>
        {s.live && (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 'var(--text-2xs)', color: 'var(--cyan-400)' }}>
            <StatusDot kind="info" pulse />
            <span style={{ letterSpacing: '0.06em', textTransform: 'uppercase' }}>live</span>
          </span>
        )}
      </div>

      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 8 }}>
        <span className="display num" style={{
          fontSize: 28, lineHeight: 1,
          fontWeight: 500,
          color: 'var(--fg-0)',
          letterSpacing: '-0.01em',
        }}>{s.count.toLocaleString('en-US')}</span>
        <span style={{ paddingBottom: 2 }}>
          <Delta value={s.delta} />
        </span>
        <div style={{ marginLeft: 'auto' }}>
          <Sparkline data={s.spark} width={68} height={22} fill="var(--violet-500)" />
        </div>
      </div>

      <div style={{ height: 1, background: 'var(--bg-3)' }} />

      <div style={{ display: 'flex', flexDirection: 'column', gap: 3, minWidth: 0 }}>
        <div className="mono" style={{
          fontSize: 'var(--text-2xs)',
          color: 'var(--fg-2)',
          letterSpacing: '0.02em',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}>{s.top}</div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
          <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>{s.topMeta}</span>
          {s.tail && (
            <span style={{ fontSize: 'var(--text-2xs)', color: s.tailKind === 'error' ? 'var(--error-500)' : 'var(--fg-2)' }}>
              {s.tail}
            </span>
          )}
        </div>
      </div>
    </a>
  );
}

export function StagesRow({ data }) {
  // data is the array from /api/home/dashboard's `stages` block. When
  // null (first paint / backend down), fall back to the demo STAGES.
  const stages = (data && data.length) ? data : STAGES;
  return (
    <div style={{ display: 'flex', gap: 12 }}>
      {stages.map(s => <StageTile key={s.id} s={s} />)}
    </div>
  );
}

// ─── GPU widget ──────────────────────────────────────────────────
function GpuWidget({ data }) {
  // data shape (from /api/home/dashboard.gpu):
  //   { name, util_pct, vram_used_mb, vram_total_mb, temp_c, power_w,
  //     power_cap_w, processes }
  // null → fall back to demo numbers (mock until a GPU is detected).
  const spark = useMemo(() => genSpark(40, 0.5, 0.35).map(v => Math.min(1, Math.max(0.1, v))), []);
  const util = data ? Math.round(data.util_pct) : 67;
  const vramUsedGB = data ? (data.vram_used_mb / 1024) : 8.4;
  const vramTotalGB = data ? (data.vram_total_mb / 1024) : 12;
  const tempC = data ? Math.round(data.temp_c) : 64;
  const powerW = data ? Math.round(data.power_w) : 142;
  const powerCapW = data ? Math.round(data.power_cap_w) : 200;
  const gpuName = data ? data.name : 'NVIDIA RTX 4070';
  const busy = util > 5;
  return (
    <div style={{
      background: 'var(--bg-1)',
      border: 'var(--border)',
      borderRadius: 'var(--radius-lg)',
      padding: '12px 14px',
      display: 'flex', flexDirection: 'column',
      gap: 10,
      minWidth: 0,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="label">GPU</span>
          <span style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-1)', fontWeight: 500 }}>{gpuName}</span>
        </div>
        {busy && (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 'var(--text-2xs)', color: 'var(--cyan-400)' }}>
            <StatusDot kind="info" pulse />
            <span style={{ letterSpacing: '0.06em', textTransform: 'uppercase' }}>busy</span>
          </span>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(60px, 0.85fr) minmax(112px, 1.4fr) minmax(56px, 0.85fr) minmax(72px, 1fr)', gap: 14, alignItems: 'end' }}>
        <GpuStat label="util" value={`${util}%`} bar={util / 100} />
        <GpuStat label="vram"
                 value={`${vramUsedGB.toFixed(1)} / ${vramTotalGB.toFixed(0)} GB`}
                 sub={`${Math.round((vramUsedGB / vramTotalGB) * 100)}%`} />
        <GpuStat label="temp" value={`${tempC}°C`}
                 sub={tempC < 75 ? 'safe' : (tempC < 85 ? 'warm' : 'hot')} />
        <GpuStat label="power" value={`${powerW} W`} sub={`of ${powerCapW} W`} />
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <Sparkline data={spark} width={180} height={20} fill="var(--cyan-500)" color="var(--cyan-500)" />
        <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>util · last 60s</span>
        <span style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>
          /Movies/Anora.mkv · whisper-large-v3
        </span>
      </div>
    </div>
  );
}

function GpuStat({ label, value, sub, bar }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
      <span className="label">{label}</span>
      <span className="display num" style={{
        fontSize: 20, lineHeight: 1,
        fontWeight: 500,
        color: 'var(--fg-0)',
        whiteSpace: 'nowrap',
        overflow: 'hidden', textOverflow: 'ellipsis',
      }}>{value}</span>
      {bar != null ? (
        <div style={{ height: 3, background: 'var(--bg-3)', borderRadius: 2, overflow: 'hidden' }}>
          <div style={{ width: `${bar * 100}%`, height: '100%', background: 'var(--violet-500)' }} />
        </div>
      ) : (
        <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>{sub}</span>
      )}
    </div>
  );
}

// ─── Service brand colors ────────────────────────────────────────
// Letterform badges — each service gets a 16px rounded square with
// the project's actual brand color + its initial. Recognizable at a
// glance, zero external assets, scales cleanly. v1.x can swap in
// vendored SVG logos if we want pixel-perfect brand marks.
const SERVICE_BRAND = {
  bazarr:   { color: '#A41818', initial: 'B' },  // Bazarr red
  sonarr:   { color: '#35C5F0', initial: 'S' },  // Sonarr blue
  radarr:   { color: '#FFC830', initial: 'R' },  // Radarr amber
  tautulli: { color: '#E8A33D', initial: 'T' },  // Tautulli orange
  subgen:   { color: 'var(--violet-500)', initial: 'σ' },  // sigma — our brand
  ollama:   { color: '#000000', initial: '🦙' },  // llama mascot
  plex:     { color: '#E5A00D', initial: 'P' },  // Plex amber
  jellyfin: { color: '#00A4DC', initial: 'J' },  // Jellyfin blue
};

function titleCase(s) {
  if (!s) return s;
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function ServiceBadge({ name, size = 16 }) {
  const brand = SERVICE_BRAND[name?.toLowerCase()] || {
    color: 'var(--bg-4)', initial: name?.[0]?.toUpperCase() || '?',
  };
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      width: size, height: size,
      borderRadius: 3,
      background: brand.color,
      color: '#fff',
      fontSize: Math.round(size * 0.6),
      fontWeight: 700,
      fontFamily: 'var(--font-display)',
      lineHeight: 1,
      flex: '0 0 auto',
    }}>{brand.initial}</span>
  );
}

// ─── Integration tile ────────────────────────────────────────────
function IntegrationTile({ i }) {
  const isError = i.status === 'error';
  return (
    <div style={{
      flex: 1, minWidth: 0,
      background: 'var(--bg-1)',
      border: 'var(--border)',
      borderRadius: 'var(--radius-lg)',
      padding: '10px 12px',
      display: 'flex', flexDirection: 'column',
      gap: 4,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <ServiceBadge name={i.name} size={16} />
        <span style={{ fontSize: 'var(--text-md)', fontWeight: 600, color: 'var(--fg-0)' }}>{titleCase(i.name)}</span>
        <StatusDot kind={i.status} />
        <span style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>{i.version || i.ver || ''}</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{
          fontSize: 'var(--text-2xs)',
          color: isError ? 'var(--error-500)' : 'var(--fg-2)',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          minWidth: 0,
        }}>{i.extra}</span>
        <span className="mono num" style={{
          fontSize: 'var(--text-2xs)',
          color: isError ? 'var(--error-500)' : 'var(--fg-3)',
          flex: '0 0 auto',
          marginLeft: 8,
        }}>{i.ping < 60 ? `${i.ping}s` : `${Math.floor(i.ping/60)}m`}</span>
      </div>
    </div>
  );
}

export function HostStrip({ integrations, gpu }) {
  // integrations: live array or null → fall back to demo INTEGRATIONS
  // gpu: live GPU snapshot or null → GpuWidget uses demo if null
  const tiles = (integrations && integrations.length) ? integrations : INTEGRATIONS;
  return (
    <div style={{ display: 'flex', gap: 12 }}>
      <div style={{ flex: '1.5 1 0', minWidth: 360 }}>
        <GpuWidget data={gpu} />
      </div>
      <div style={{ flex: '3 1 0', display: 'flex', gap: 12, minWidth: 0 }}>
        {tiles.map(i => <IntegrationTile key={i.name} i={i} />)}
      </div>
    </div>
  );
}

// ─── Next scheduled run ──────────────────────────────────────────
function NextRunCard({ data }) {
  // data shape (from /api/home/dashboard.next_run):
  //   { enabled, rule, mode, targets, next_run_at, countdown_s,
  //     last_run_at, last_result }
  // When enabled=false (no schedule yet), the card shows a "not
  // configured" state instead of the live values.
  const enabled = data ? data.enabled : true;
  const rule = (data && data.rule) || 'nightly_walk';
  const mode = (data && data.mode) || 'manual_confirm';
  const targets = (data && data.targets && data.targets.length)
                   ? data.targets : ['/TV', '/Movies', '/Anime'];
  const countdown = data && data.countdown_s != null
                     ? fmtCountdown(data.countdown_s) : '4h 12m';
  const localClock = data && data.next_run_at
                     ? fmtTimeFromTs(data.next_run_at)
                     : '18:44';
  const lastRun = data && data.last_run_at
                   ? `${fmtTimeFromTs(data.last_run_at)} · ${data.last_result || '—'}`
                   : '10:32 · wrote 39 · skipped 8';
  return (
    <div style={{
      background: 'var(--bg-1)',
      border: 'var(--border)',
      borderRadius: 'var(--radius-lg)',
      padding: 16,
      display: 'flex', flexDirection: 'column',
      gap: 12,
      width: 320,
      flex: '0 0 320px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span className="label">Next scheduled run</span>
        <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>{localClock} local</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span className="display num" style={{
          fontSize: 32, lineHeight: 1,
          fontWeight: 500,
          letterSpacing: '-0.01em',
          color: enabled ? 'var(--fg-0)' : 'var(--fg-3)',
        }}>{enabled ? countdown : 'disabled'}</span>
      </div>
      <div style={{ height: 1, background: 'var(--bg-3)' }} />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <KV k="rule"    v={<span className="mono">{rule}</span>} />
        <KV k="mode"    v={<span className="mono">{mode}</span>} />
        <KV k="targets" v={<span className="mono" style={{ color: 'var(--fg-1)' }}>{targets.join(', ')}</span>} />
        <KV k="last run" v={<span style={{ color: 'var(--fg-3)' }} className="mono">{lastRun}</span>} />
      </div>
      <div style={{ flex: 1 }} />
      <div style={{ display: 'flex', gap: 8 }}>
        <button className="btn primary" style={{ flex: 1 }}>Run now</button>
        <button className="btn">Edit rule</button>
      </div>
    </div>
  );
}

function fmtCountdown(sec) {
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m`;
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return `${h}h ${m.toString().padStart(2, '0')}m`;
}
function fmtTimeFromTs(ts) {
  const d = new Date(ts * 1000);
  return `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
}

function KV({ k, v }) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12, minWidth: 0 }}>
      <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>{k}</span>
      <span style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-1)', textAlign: 'right', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{v}</span>
    </div>
  );
}

// ─── Recent activity ─────────────────────────────────────────────
function ActivityRow({ a, last }) {
  const ks = KIND_STYLE[a.kind] || { dot: 'muted', fg: 'var(--fg-1)' };
  const isFailed = a.kind === 'failed';
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '14px 60px 96px 1fr auto 16px',
      alignItems: 'center',
      gap: 10,
      padding: '6px 16px',
      borderBottom: last ? 'none' : '1px solid var(--bg-3)',
      transition: 'background var(--dur-fast)',
    }}
    onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-2)'}
    onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
      <StatusDot kind={ks.dot} />
      <span className="mono num" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>{a.t}</span>
      <span style={{ fontSize: 'var(--text-sm)', color: ks.fg, fontWeight: a.kind === 'failed' || a.kind === 'written-back' ? 500 : 400 }}>
        {a.kind}
      </span>
      <span className="mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-1)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>
        {a.path}
      </span>
      <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: isFailed ? 'var(--error-500)' : 'var(--fg-2)', whiteSpace: 'nowrap' }}>{a.meta}</span>
      <span style={{ color: 'var(--fg-3)', fontSize: 'var(--text-sm)', textAlign: 'center' }}>⋯</span>
    </div>
  );
}

function ActivityCard({ data }) {
  // data: live array from /api/home/dashboard.activity, or null → fall
  // back to demo ACTIVITY.
  const rows = (data && data.length) ? data : ACTIVITY;
  return (
    <div style={{
      flex: 1, minWidth: 0,
      background: 'var(--bg-1)',
      border: 'var(--border)',
      borderRadius: 'var(--radius-lg)',
      display: 'flex', flexDirection: 'column',
      overflow: 'hidden',
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10,
        padding: '12px 16px',
        borderBottom: 'var(--border)',
      }}>
        <span className="label">Recent activity</span>
        <span style={{ flex: 1 }} />
        <div style={{ display: 'flex', gap: 6 }}>
          <span className="chip">all</span>
          <span className="chip" style={{ background: 'transparent', color: 'var(--fg-2)' }}>failed</span>
          <span className="chip" style={{ background: 'transparent', color: 'var(--fg-2)' }}>written-back</span>
        </div>
        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>showing last {rows.length}</span>
      </div>
      <div>
        {rows.length === 0 ? (
          <div style={{ padding: '24px 16px', color: 'var(--fg-3)', fontSize: 'var(--text-sm)', textAlign: 'center' }}>
            no activity yet — submit a scan to get started.
          </div>
        ) : (
          rows.map((a, i) => <ActivityRow key={a.ledger_id || i} a={a} last={i === rows.length - 1} />)
        )}
      </div>
      <div style={{ marginTop: 'auto', padding: '8px 16px', borderTop: '1px solid var(--bg-3)', display: 'flex', alignItems: 'center' }}>
        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>View full activity →</span>
      </div>
    </div>
  );
}

export function NextRunActivitySplit({ nextRun, activity }) {
  return (
    <div style={{ display: 'flex', gap: 12, flex: 1, minHeight: 0 }}>
      <NextRunCard data={nextRun} />
      <ActivityCard data={activity} />
    </div>
  );
}

