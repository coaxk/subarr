// Home dashboard body — stages, host telemetry, next run + activity.

import { Sparkline, Delta, StatusDot, Glyph, genSpark } from './atoms.jsx';

// Hooks come from the global React (CDN). Under the old in-browser
// Babel pipeline this destructure lived in atoms.jsx and leaked across
// the shared global scope; under the ESM bundle each module needs its
// own.
const { useMemo, useState, useCallback } = React;

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
    label: 'transcribing',
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
// "Run now" POSTs /api/schedule/coverage_walk/run-now (same endpoint
// Coverage's Re-walk uses) — the dashboard primary CTA is "trigger a
// scheduled walk now," not editing the rule from here. Edit rule
// jumps to the Rules page.
//
// 1h/24h/7d time range toggle removed in v1.0: the backend snapshot
// endpoint only returns current state, not historical buckets. Adding
// a fake toggle would mislead. Re-add in v1.1 when historical
// retention ships.
export function PageHeader({ now }) {
  const [running, setRunning] = useState(false);
  const runNow = useCallback(async () => {
    setRunning(true);
    try {
      const r = await fetch('/api/schedule/coverage_walk/run-now', {
        method: 'POST', credentials: 'same-origin',
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
    } catch (e) {
      alert(`Run now failed: ${e.message}`);
    } finally {
      setRunning(false);
    }
  }, []);

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
          fontSize: 'var(--text-h1)', lineHeight: 'var(--lh-h1)',
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
      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <a href="/rules"
          title="Open the Rules page to change schedule cadence or filters"
          style={{
            fontSize: 'var(--text-xs)', color: 'var(--fg-3)',
            textDecoration: 'underline', textUnderlineOffset: 3,
            textDecorationStyle: 'dotted', textDecorationColor: 'var(--bg-5)',
          }}>
          edit rule
        </a>
        <button className="btn primary" onClick={runNow} disabled={running}
          title="Trigger an immediate coverage walk now — Sonarr/Radarr/Bazarr/ffprobe pass. Doesn't change the scheduled cadence.">
          {running ? 'Running…' : 'Run now'}
        </button>
      </div>
    </div>
  );
}

// ─── Stage tile ──────────────────────────────────────────────────
// Each stage tile links to the page where users can do something with
// that stage's contents. Mapping is intentional, not generic:
//   discovered  → /library      (browse what's been discovered)
//   probing     → /coverage     (in-flight probes are the candidate list)
//   wanted      → /coverage     (gaps)
//   scanning    → /queue        (active jobs)
//   scanned/written-back → /file-modal (completed ledger)
const STAGE_HREF = {
  discovered: '/library',
  probing: '/coverage',
  wanted: '/coverage',
  'bazarr-wanted': '/coverage',
  scanning: '/queue',
  scanned: '/file-modal',
  'written-back': '/file-modal',
};

const STAGE_TIPS = {
  discovered:  'Total files subarr has indexed (probed or pending). Includes both video files and subtitle sidecars subarr has noticed.',
  probing:     "Files currently being ffprobed by an active probe walk. Probe walks are separate from coverage walks — they only run after coverage discovery completes, or when you trigger one manually from Library. 0 here means no probe walks are active right now, not that something's broken.",
  wanted:      'Bazarr-wanted entries — subtitles missing per Bazarr. Includes both actionable (file exists) and pending-download (file not yet imported).',
  scanning:    'Files currently in subgen\'s transcribe queue or actively being transcribed.',
  written:     'Completed transcribes — subarr generated a subtitle file and (where possible) uploaded it directly to Bazarr.',
};
function StageTile({ s }) {
  const href = STAGE_HREF[s.id] || '/coverage';
  return (
    <a className="stage-tile" href={href} title={STAGE_TIPS[s.id] || s.label} style={{
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
          fontSize: 'var(--text-display-xl)', lineHeight: 1,
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
  // data is the array from /api/home/dashboard's `stages` block.
  // Render nothing (skeleton) until live data arrives — no more demo fallback.
  const stages = (data && data.length) ? data : [];
  if (!stages.length) {
    return (
      <div style={{ display: 'flex', gap: 12 }}>
        {[0,1,2,3,4].map(i => (
          <div key={i} style={{
            flex: 1, height: 92, background: 'var(--bg-1)',
            border: 'var(--border)', borderRadius: 'var(--radius-lg)',
            opacity: 0.4,
          }} />
        ))}
      </div>
    );
  }
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
        <GpuStat label="util" value={`${util}%`} bar={util / 100}
                 tip="GPU compute utilization. 100% = fully loaded. Whisper transcription typically runs at 80-100%." />
        <GpuStat label="vram"
                 value={`${vramUsedGB.toFixed(1)} / ${vramTotalGB.toFixed(0)} GB`}
                 sub={`${Math.round((vramUsedGB / vramTotalGB) * 100)}%`}
                 tip="VRAM (GPU memory) in use vs total. Whisper large-v3 needs ~5GB, Ollama models 2-8GB depending on size. If both are loaded simultaneously you can OOM — subarr can unload Ollama before transcribe." />
        <GpuStat label="temp" value={`${tempC}°C`}
                 sub={tempC < 75 ? 'safe' : (tempC < 85 ? 'warm' : 'hot')}
                 tip="GPU core temperature. <75°C safe, 75-85°C warm, >85°C may throttle." />
        <GpuStat label="power" value={`${powerW} W`} sub={`of ${powerCapW} W`}
                 tip="Current power draw vs configured limit. Sustained near-limit = card is working hard." />
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

function GpuStat({ label, value, sub, bar, tip }) {
  return (
    <div title={tip} style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0, cursor: tip ? 'help' : 'default' }}>
      <span className="label">{label}</span>
      <span className="display num" style={{
        fontSize: 'var(--text-xl)', lineHeight: 1,
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
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
        <ServiceBadge name={i.name} size={16} />
        <span style={{
          fontSize: 'var(--text-md)', fontWeight: 600, color: 'var(--fg-0)',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          minWidth: 0, flex: '0 1 auto',
        }}>{titleCase(i.name)}</span>
        <span title={
          i.status === 'ok' ? 'Online and responding' :
          i.status === 'error' ? 'Configured but unreachable / returning errors' :
          i.status === 'muted' ? 'Not configured — set URL + API key in .env' :
          `Status: ${i.status}`
        }>
          <StatusDot kind={i.status} />
        </span>
        <span style={{ flex: 1, minWidth: 4 }} />
        <span className="mono"
          title={`${titleCase(i.name)} version reported by /system/status`}
          style={{
            fontSize: 'var(--text-2xs)', color: 'var(--fg-3)',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            minWidth: 0, flex: '0 1 auto', maxWidth: '50%',
          }}>{i.version || i.ver || ''}</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', minWidth: 0, gap: 8 }}>
        <span title={i.extra} style={{
          fontSize: 'var(--text-2xs)',
          color: isError ? 'var(--error-500)' : 'var(--fg-2)',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          minWidth: 0, flex: '1 1 auto',
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
  // integrations: live array — no more demo fallback.
  // gpu: live GPU snapshot or null → GpuWidget uses demo if null
  const tiles = (integrations && integrations.length) ? integrations : [];
  return (
    <div style={{ display: 'flex', gap: 12 }}>
      <div style={{ flex: '1.5 1 0', minWidth: 360 }}>
        <GpuWidget data={gpu} />
      </div>
      {/* 3×2 grid — wider tiles, breathes better than 6-wide strip. */}
      <div style={{
        flex: '3 1 0', minWidth: 0,
        display: 'grid',
        gridTemplateColumns: 'repeat(3, 1fr)',
        gridAutoRows: '1fr',
        gap: 8,
      }}>
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
          fontSize: 'var(--text-display-2xl)', lineHeight: 1,
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
      {/* Buttons sit right after the KV rows (no flex:1 spacer). When
          the parent row drives sizing, the spacer would create a big
          visual gap between 'last run' and the buttons. */}
      <div style={{ display: 'flex', gap: 8 }}>
        <NextRunActions />
      </div>
    </div>
  );
}

// Shared between the dashboard header and the next-run sidebar.
function NextRunActions() {
  const [running, setRunning] = useState(false);
  const runNow = useCallback(async () => {
    setRunning(true);
    try {
      const r = await fetch('/api/schedule/coverage_walk/run-now', {
        method: 'POST', credentials: 'same-origin',
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
    } catch (e) {
      alert(`Run now failed: ${e.message}`);
    } finally {
      setRunning(false);
    }
  }, []);
  return (
    <>
      <button className="btn primary" style={{ flex: 1 }} onClick={runNow} disabled={running}>
        {running ? 'Running…' : 'Run now'}
      </button>
      <a href="/rules" className="btn" style={{ textDecoration: 'none' }}>Edit rule</a>
    </>
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

const ACTIVITY_FILTERS = [
  { id: 'all', label: 'all', test: () => true },
  { id: 'failed', label: 'failed', test: (a) => a.kind === 'failed' },
  { id: 'written-back', label: 'written-back', test: (a) => a.kind === 'written-back' },
];

function ActivityCard({ data }) {
  // data: live array from /api/home/dashboard.activity, or null → fall
  // back to demo ACTIVITY.
  const allRows = (data && data.length) ? data : ACTIVITY;
  const [filter, setFilter] = useState('all');
  const rows = allRows.filter((ACTIVITY_FILTERS.find((f) => f.id === filter) || ACTIVITY_FILTERS[0]).test);

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
        padding: 'var(--row-cozy)',
        borderBottom: 'var(--border)',
      }}>
        <span className="label">Recent activity</span>
        <span style={{ flex: 1 }} />
        <div style={{ display: 'flex', gap: 6 }}>
          {ACTIVITY_FILTERS.map((f) => (
            <span
              key={f.id}
              onClick={() => setFilter(f.id)}
              className={`chip ${filter === f.id ? '' : ''}`}
              style={{
                cursor: 'pointer',
                background: filter === f.id ? undefined : 'transparent',
                color: filter === f.id ? undefined : 'var(--fg-2)',
              }}>
              {f.label}
            </span>
          ))}
        </div>
        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>showing {rows.length}</span>
      </div>
      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
        {rows.length === 0 ? (
          <div style={{ padding: '24px 16px', color: 'var(--fg-3)', fontSize: 'var(--text-sm)', textAlign: 'center' }}>
            {allRows.length === 0
              ? 'no activity yet — submit a scan to get started.'
              : `no ${filter} activity in the recent window.`}
          </div>
        ) : (
          rows.map((a, i) => <ActivityRow key={a.ledger_id || i} a={a} last={i === rows.length - 1} />)
        )}
      </div>
      <div style={{ padding: 'var(--row-dense)', borderTop: '1px solid var(--bg-3)', display: 'flex', alignItems: 'center', flex: '0 0 auto' }}>
        <a href="/file-modal" style={{
          fontSize: 'var(--text-xs)',
          color: 'var(--fg-2)',
          textDecoration: 'none',
        }}>View full activity →</a>
      </div>
    </div>
  );
}

// v1.0 #147: post-onboarding "what next" guidance. Shown for 7 days after
// completion, dismissible (localStorage). Walks the user through the
// first 4 things to do once setup finishes. Prevents the "now what?"
// abandonment cliff at first-run.
export function WelcomeCard() {
  const [onboard, setOnboard] = React.useState(null);
  const [pendingCount, setPendingCount] = React.useState(0);
  const [dismissed, setDismissed] = React.useState(false);

  React.useEffect(() => {
    try {
      const ls = localStorage.getItem('subarr.welcome.dismissed');
      if (ls === '1') setDismissed(true);
    } catch {}
    fetch('/api/onboarding/state', { credentials: 'same-origin' })
      .then(r => r.ok ? r.json() : null)
      .then(setOnboard).catch(() => {});
    fetch('/api/audio-lang/pending-review', { credentials: 'same-origin' })
      .then(r => r.ok ? r.json() : null)
      .then(d => setPendingCount(d?.count || 0)).catch(() => {});
  }, []);

  if (dismissed || !onboard?.completed_at) return null;
  const daysSince = (Date.now() / 1000 - onboard.completed_at) / 86400;
  if (daysSince > 7) return null;

  const dismiss = () => {
    try { localStorage.setItem('subarr.welcome.dismissed', '1'); } catch {}
    setDismissed(true);
  };

  const steps = [
    {
      icon: '▶',
      title: 'Run your first coverage walk',
      copy: 'subarr will check Bazarr, Sonarr and Radarr to find files missing subs.',
      // Match the PageHeader Run-now pattern: credentials, ok check, and
      // don't announce success unless the POST actually succeeded.
      // Previously a 401/500 still showed "Coverage walk started", which
      // is the worst kind of broken — the user thinks they're done.
      cta: { label: 'Run now', href: '#run-now', onClick: async (e) => {
        e.preventDefault();
        try {
          const r = await fetch('/api/schedule/coverage_walk/run-now', {
            method: 'POST', credentials: 'same-origin',
          });
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          alert('Coverage walk started. Watch the Coverage page.');
        } catch (err) {
          alert(`Run now failed: ${err.message || err}`);
        }
      }},
    },
    pendingCount > 0 ? {
      icon: '⚠',
      title: `Review ${pendingCount} files needing language verification`,
      copy: "subarr can't tell the audio language for these files. A 30-second listen each gets them right.",
      cta: { label: 'Open Coverage', href: '/coverage' },
    } : {
      icon: '🎯',
      title: 'Open the Coverage page',
      copy: "See the prioritised list of files that need subtitle work.",
      cta: { label: 'Open Coverage', href: '/coverage' },
    },
    {
      icon: '📊',
      title: 'Check your provider leaderboard',
      copy: 'See which Bazarr providers actually work for your library.',
      cta: { label: 'Open leaderboard', href: '/settings#providers' },
    },
    {
      icon: '🛠️',
      title: 'Set up auto-queue rules',
      copy: 'Let subarr automatically queue files matching criteria you choose.',
      cta: { label: 'Open Rules', href: '/rules' },
    },
  ];

  return (
    <div style={{
      background: 'linear-gradient(135deg, rgba(139,92,246,0.10), rgba(34,211,161,0.05))',
      border: '1px solid rgba(139,92,246,0.30)',
      borderRadius: 'var(--radius-lg)',
      padding: '18px 20px',
      display: 'flex', flexDirection: 'column', gap: 14,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span style={{ fontSize: 22 }}>🎉</span>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 'var(--text-lg)', fontWeight: 600, color: 'var(--fg-0)' }}>
            Welcome to subarr.
          </div>
          <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>
            Setup is complete. Here are the first few things to do to get the most out of it.
          </div>
        </div>
        <button className="btn ghost" onClick={dismiss}
          title="Hide this card. You can re-trigger it by re-running onboarding."
          style={{ fontSize: 'var(--text-2xs)' }}>
          got it
        </button>
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
              <a href={s.cta.href} onClick={s.cta.onClick}
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

export function NextRunActivitySplit({ nextRun, activity }) {
  // Row is sized by NextRunCard's natural content height. We removed
  // the earlier flex:1 because it made the row claim main-canvas's
  // leftover vertical space, which over-stretched both cards and left
  // a big visual gap above the Run now / Edit rule buttons inside the
  // next-run card. With alignItems:stretch (default), the activity
  // card matches next-run's content height and scrolls internally.
  return (
    <div style={{ display: 'flex', gap: 12, alignItems: 'stretch' }}>
      <NextRunCard data={nextRun} />
      <ActivityCard data={activity} />
    </div>
  );
}

