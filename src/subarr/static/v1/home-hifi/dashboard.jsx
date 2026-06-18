// Home dashboard body — stages, host telemetry, next run + activity.

import { Sparkline, Delta, StatusDot, Glyph } from './atoms.jsx';
import { useLiveChromeCounts } from './chrome.jsx';

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
  // #97: transcribing tile renders an active-vs-queued split when the
  // payload carries the new fields (the scanning stage). txTotal drives
  // the gradient activity bar.
  const isTranscribing = s.active != null;
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

      {isTranscribing ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div style={{ display: 'flex', alignItems: 'flex-end', gap: 8 }}>
            <span className="display num" style={{
              fontSize: 'var(--text-display-xl)', lineHeight: 1,
              fontWeight: 500, color: 'var(--fg-0)', letterSpacing: '-0.01em',
            }}>{s.active || 0}</span>
            <span style={{ paddingBottom: 4, fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>active</span>
            <div style={{ marginLeft: 'auto', textAlign: 'right', lineHeight: 1.15 }}>
              <div className="num" style={{ fontSize: 'var(--text-lg)', color: 'var(--fg-1)', fontWeight: 500 }}>{s.queued || 0}</div>
              <div style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>queued</div>
            </div>
          </div>
          {/* #97: live progress of the CURRENT job (subgen %), not x-of-y.
              null while idle / before the first % line — bar sits at 0. */}
          <div style={{ height: 4, borderRadius: 2, background: 'var(--bg-3)', overflow: 'hidden' }}>
            <div style={{
              width: `${s.progress != null ? Math.max(0, Math.min(100, s.progress)) : 0}%`,
              height: '100%',
              background: 'linear-gradient(90deg, var(--violet-500), var(--cyan-400))',
              transition: 'width var(--dur-med, 240ms) var(--ease-out)',
            }} />
          </div>
        </div>
      ) : (
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
      )}

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

// #98: fixed left→right panel order — transcribing, bazarr-wanted,
// discovered, written-back, probing. Backend may emit either id variant
// (e.g. 'scanning'/'transcribing', 'wanted'/'bazarr-wanted'), so map both.
const STAGE_ORDER = {
  scanning: 0, transcribing: 0,
  wanted: 1, 'bazarr-wanted': 1,
  discovered: 2,
  written: 3, 'written-back': 3,
  probing: 4,
};
export function StagesRow({ data }) {
  // data is the array from /api/home/dashboard's `stages` block.
  // Render nothing (skeleton) until live data arrives — no more demo fallback.
  const stages = ((data && data.length) ? [...data] : [])
    .sort((a, b) => (STAGE_ORDER[a.id] ?? 99) - (STAGE_ORDER[b.id] ?? 99));
  if (!stages.length) {
    return (
      <div data-testid="dashboard-stages" style={{ display: 'flex', gap: 12 }}>
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
    <div data-testid="dashboard-stages" style={{ display: 'flex', gap: 12 }}>
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
  // The util sparkline shows real history only — the backend doesn't
  // emit util_history yet, so it stays hidden rather than animating
  // fabricated noise next to live numbers. Wired the moment the payload
  // carries history.
  // #99: accumulate util history client-side. The backend doesn't emit
  // util_history; the dashboard polls every 5s, so we keep a rolling
  // 60-sample buffer — a REAL utilisation graph over time, not fabricated
  // noise. (Falls back to data.util_history if the backend ever sends it.)
  const [utilHist, setUtilHist] = useState(() =>
    (data && Array.isArray(data.util_history)) ? data.util_history.slice(-60) : []);
  React.useEffect(() => {
    if (!data || data.util_pct == null) return;
    setUtilHist((h) => [...h.slice(-59), Math.round(data.util_pct)]);
  }, [data]);
  const spark = utilHist;
  // #193: no GPU data → an honest "no data" state, never demo numbers.
  // (CPU-only boxes and subgen-down installs were seeing a fabricated
  // RTX 4070 at 67% util.)
  if (!data) {
    return (
      <div data-testid="dashboard-gpu" style={{
        background: 'var(--bg-1)',
        border: 'var(--border)',
        borderRadius: 'var(--radius-lg)',
        padding: '12px 14px',
        display: 'flex', flexDirection: 'column', gap: 10,
        minWidth: 0, height: '100%', justifyContent: 'space-between',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="label">GPU</span>
        </div>
        <div style={{ color: 'var(--fg-3)', fontSize: 'var(--text-sm)' }}>
          no GPU telemetry — none detected, or subgen is unreachable.
        </div>
        <div style={{ height: 44 }} />
      </div>
    );
  }
  const util = Math.round(data.util_pct);
  const vramUsedGB = data.vram_used_mb / 1024;
  const vramTotalGB = data.vram_total_mb / 1024;
  const tempC = Math.round(data.temp_c);
  const powerW = Math.round(data.power_w);
  const powerCapW = Math.round(data.power_cap_w);
  const gpuName = data.name;
  const busy = util > 5;
  return (
    <div data-testid="dashboard-gpu" style={{
      background: 'var(--bg-1)',
      border: 'var(--border)',
      borderRadius: 'var(--radius-lg)',
      padding: '12px 14px',
      display: 'flex', flexDirection: 'column',
      gap: 10,
      minWidth: 0,
      // Fill the row height (the integration-tile grid beside it is
      // taller) and spread header / stats so the box doesn't leave dead
      // space now that the util sparkline row is gone.
      height: '100%',
      justifyContent: 'space-between',
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
        <GpuStat label="util" value={`${util}%`}
                 sub={busy ? 'busy' : 'idle'}
                 tip="GPU compute utilization. 100% = fully loaded. Whisper transcription typically runs at 80-100%. Graphed over time below." />
        <GpuStat label="vram"
                 value={`${vramUsedGB.toFixed(1)} / ${vramTotalGB.toFixed(0)} GB`}
                 bar={Math.min(vramUsedGB / vramTotalGB, 1)}
                 tip="VRAM (GPU memory) in use vs total. Whisper large-v3 needs ~5GB, Ollama models 2-8GB depending on size. If both are loaded simultaneously you can OOM — subarr can unload Ollama before transcribe." />
        <GpuStat label="temp" value={`${tempC}°C`}
                 sub={tempC < 75 ? 'safe' : (tempC < 85 ? 'warm' : 'hot')}
                 tip="GPU core temperature. <75°C safe, 75-85°C warm, >85°C may throttle." />
        <GpuStat label="power" value={`${powerW} W`} sub={`of ${powerCapW} W`}
                 tip="Current power draw vs configured limit. Sustained near-limit = card is working hard." />
      </div>

      {/* #99: util graph fills the widget's bottom space. Responsive width
          (scales to the flexible widget); height fixed. Real client-side
          history — shows a placeholder until the first couple of samples. */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span className="label">util · over time</span>
          {spark.length >= 2 && (
            <span className="num" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>
              ~{Math.max(1, Math.round(spark.length * 5 / 60))}m
            </span>
          )}
        </div>
        {spark.length >= 2 ? (
          <Sparkline data={spark} width={520} height={44} responsive
                     fill="var(--violet-500)" color="var(--violet-500)" />
        ) : (
          <div style={{ height: 44, display: 'flex', alignItems: 'center',
            color: 'var(--fg-3)', fontSize: 'var(--text-2xs)' }}>
            collecting utilisation…
          </div>
        )}
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
  // data: live array from /api/home/dashboard.activity. #193: empty/null
  // renders the honest empty state below — NEVER demo rows. A new install
  // saw fabricated activity for files that don't exist and reasonably
  // concluded subarr was writing mystery files.
  const allRows = data || [];
  const [filter, setFilter] = useState('all');
  // #100: cap to a handful + no inner scroll. "View full activity" already
  // covers the expanded view, and a shorter card rolls the bottom of the
  // page up, tightening the empty space beside the next-run card.
  const rows = allRows
    .filter((ACTIVITY_FILTERS.find((f) => f.id === filter) || ACTIVITY_FILTERS[0]).test)
    .slice(0, 7);

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
      <div>
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

// #203: "what you're missing" update nudge. The bare version chip didn't
// convert (the fleet sat on 1.1.0 for weeks), so when an update exists we
// show the missed RELEASE TITLES — our titles are descriptive, so they ARE
// the digest. Dismissal is keyed to the latest version: each new release
// re-surfaces the card once.
// #238: subarr ships with no auth by default. A default install is reachable
// by anyone who can hit the port — and the API drives Sonarr/Radarr, restarts
// subgen, edits library roots. This warns once (dismissible) so a new install
// makes an INFORMED choice rather than being silently wide open. Hidden the
// moment any auth is configured (api key or HTTP Basic) — auth-status reports it.
export function NoAuthWarningCard() {
  const [show, setShow] = React.useState(false);

  React.useEffect(() => {
    fetch('/api/auth-status', { credentials: 'same-origin' })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!d || d.configured) return;
        try {
          if (localStorage.getItem('subarr.noAuthBanner.dismissed') === '1') return;
        } catch {}
        setShow(true);
      })
      .catch(() => {});
  }, []);

  if (!show) return null;
  const dismiss = () => {
    try { localStorage.setItem('subarr.noAuthBanner.dismissed', '1'); } catch {}
    setShow(false);
  };

  return (
    <div style={{
      background: 'rgba(245,158,11,0.08)',
      border: '1px solid var(--warn-500, rgba(245,158,11,0.45))',
      borderRadius: 'var(--radius-lg)',
      padding: '14px 18px',
      display: 'flex', alignItems: 'center', gap: 12,
    }}>
      <span style={{ fontSize: 18 }}>⚠️</span>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 'var(--text-md)', fontWeight: 600, color: 'var(--fg-0)' }}>
          No authentication is configured
        </div>
        <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
          Anyone who can reach this address can control your library, trigger Sonarr/Radarr, and change settings.
          On a trusted home LAN that may be fine. If subarr is reachable from anywhere else, set{' '}
          <span className="mono">SUBARR_API_KEY</span> (or put it behind a reverse proxy with auth).
        </div>
      </div>
      <a className="btn sm" href="https://github.com/coaxk/subarr#security" target="_blank" rel="noreferrer">
        How to secure it
      </a>
      <button className="btn sm ghost" onClick={dismiss} title="I understand — hide this">
        dismiss
      </button>
    </div>
  );
}


export function UpdateNudgeCard() {
  const [subarrState, setSubarrState] = React.useState(null);
  const [dismissed, setDismissed] = React.useState(false);

  React.useEffect(() => {
    fetch('/api/updates', { credentials: 'same-origin' })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        const s = ((d && d.products) || []).find((p) => p.product === 'subarr');
        if (!s || !s.has_update) return;
        try {
          if (localStorage.getItem(`subarr.updateNudge.dismissed.${s.latest_version}`) === '1') {
            setDismissed(true);
          }
        } catch {}
        setSubarrState(s);
      })
      .catch(() => {});
  }, []);

  if (!subarrState || dismissed) return null;
  const missed = subarrState.missed_releases || [];
  const shown = missed.slice(0, 5);
  const more = missed.length - shown.length;
  const dismiss = () => {
    try { localStorage.setItem(`subarr.updateNudge.dismissed.${subarrState.latest_version}`, '1'); } catch {}
    setDismissed(true);
  };

  return (
    <div style={{
      background: 'linear-gradient(135deg, rgba(34,211,161,0.08), rgba(139,92,246,0.05))',
      border: '1px solid rgba(34,211,161,0.30)',
      borderRadius: 'var(--radius-lg)',
      padding: '14px 18px',
      display: 'flex', flexDirection: 'column', gap: 10,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span style={{ fontSize: 18 }}>⬆️</span>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 'var(--text-md)', fontWeight: 600, color: 'var(--fg-0)' }}>
            {subarrState.latest_version} is out — you're on {subarrState.current_version}
            {missed.length > 1 ? ` (${missed.length} releases behind)` : ''}
          </div>
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
            Upgrade with <span className="mono">docker compose pull && docker compose up -d</span> — no migration, no config changes.
          </div>
        </div>
        {subarrState.release_notes_url && (
          <a className="btn sm" href={subarrState.release_notes_url} target="_blank" rel="noreferrer">
            Release notes
          </a>
        )}
        <button className="btn sm ghost" onClick={dismiss} title="Hide until the next release">
          dismiss
        </button>
      </div>
      {shown.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, paddingLeft: 30 }}>
          {shown.map((m) => (
            <div key={m.tag} style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-1)' }}>
              {m.notes_url
                ? <a href={m.notes_url} target="_blank" rel="noreferrer" style={{ color: 'var(--fg-1)', textDecoration: 'none' }}>• {m.title || m.tag}</a>
                : <>• {m.title || m.tag}</>}
            </div>
          ))}
          {more > 0 && (
            <div style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>…and {more} more</div>
          )}
        </div>
      )}
    </div>
  );
}


// v1.0 #147: post-onboarding "what next" guidance. Shown for 7 days after
// completion, dismissible (localStorage). Walks the user through the
// first 4 things to do once setup finishes. Prevents the "now what?"
// abandonment cliff at first-run.
// ─── First-walk empty state (#202 activation) ────────────────────
// The dashboard's honest "nothing has happened yet" signal: no stage has a
// non-zero count and the activity feed is empty. (If Bazarr-wanted already
// populates, the user has data → no CTA.) Pure + exported for unit testing.
export function shouldShowFirstWalkCta(data) {
  if (!data) return false; // not loaded yet — don't flash the CTA
  const stages = (data.stages || []);
  const anyStage = stages.some((s) => s && (s.count || 0) > 0);
  const activity = (data.activity || []);
  return !anyStage && activity.length === 0;
}

// Safety net for anyone who lands on an empty dashboard (no-arr, auto-walk
// error, opted out, established install). One click triggers the same coverage
// walk the header's Run-now uses.
export function FirstWalkCta({ data }) {
  const [running, setRunning] = useState(false);
  const [msg, setMsg] = useState(null);
  if (!shouldShowFirstWalkCta(data)) return null;
  const run = async () => {
    setRunning(true);
    setMsg(null);
    try {
      const r = await fetch('/api/schedule/coverage_walk/run-now', {
        method: 'POST',
        credentials: 'same-origin',
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setMsg('Coverage walk started — results will appear here shortly.');
    } catch (e) {
      setMsg(`Couldn't start the walk: ${e.message}`);
    } finally {
      setRunning(false);
    }
  };
  return (
    <div style={{
      background: 'var(--bg-1)', border: 'var(--border)', borderRadius: 'var(--radius-lg)',
      padding: '22px 24px', display: 'flex', flexDirection: 'column', gap: 10,
    }}>
      <div style={{ fontSize: 'var(--text-h3)', fontWeight: 700, color: 'var(--fg-0)' }}>
        No coverage data yet
      </div>
      <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)', lineHeight: 1.5, maxWidth: 560 }}>
        Run your first coverage walk — subarr checks Bazarr/Sonarr/Radarr and ffprobes your
        library to find which episodes and movies are missing subtitles. This is where subarr
        starts earning its keep.
      </div>
      <div>
        <button className="btn violet" onClick={run} disabled={running}>
          {running ? 'Starting…' : 'Run your first walk'}
        </button>
      </div>
      {msg && <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>{msg}</div>}
    </div>
  );
}

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

// ─── Aftercare panel ─────────────────────────────────────────────
// #156: compact attention banner shown when post-transcription review
// items are waiting. Reads aftercare_count from the shared chrome
// counts singleton — no second poller. Hidden when count is 0.
export function AfterCarePanel() {
  const counts = useLiveChromeCounts();
  const count = counts.aftercare_count || 0;
  if (!count) return null;
  const label = count === 1 ? '1 job needs review' : `${count} jobs need review`;
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      gap: 16,
      background: 'linear-gradient(90deg, rgba(139,92,246,0.12), rgba(139,92,246,0.06))',
      border: '1px solid rgba(139,92,246,0.35)',
      borderRadius: 'var(--radius-lg)',
      padding: '10px 16px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <StatusDot kind="violet" pulse />
        <span style={{ fontSize: 'var(--text-sm)', fontWeight: 500, color: 'var(--fg-0)' }}>
          {label}
        </span>
        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
          — post-transcription quality review is ready
        </span>
      </div>
      <a href="/aftercare" style={{
        fontSize: 'var(--text-xs)', fontWeight: 600,
        color: 'var(--violet-400)',
        textDecoration: 'none',
        whiteSpace: 'nowrap',
        padding: '4px 10px',
        border: '1px solid rgba(139,92,246,0.40)',
        borderRadius: 'var(--radius-md)',
        background: 'rgba(139,92,246,0.10)',
        transition: 'background var(--dur-fast)',
      }}>
        Review →
      </a>
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
  //
  // flexShrink:0 is REQUIRED: main-canvas is a flex column with
  // overflow-y:auto, so its default flex-shrink:1 will squeeze this
  // last row below its content height when the page overflows. The
  // activity card hides its overflow and scrolls, but the next-run
  // card has no overflow guard, so it would spill its content past
  // the card border. Pinning shrink to 0 keeps the row at natural
  // height and lets main-canvas scroll instead.
  return (
    <div style={{ display: 'flex', gap: 12, alignItems: 'stretch', flexShrink: 0 }}>
      <NextRunCard data={nextRun} />
      <ActivityCard data={activity} />
    </div>
  );
}

