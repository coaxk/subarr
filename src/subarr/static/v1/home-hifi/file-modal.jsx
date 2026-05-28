// Per-file verdict modal — over an Activity page surface.

import { StatusDot } from './atoms.jsx';

const { useState } = React;

const FILE_EVENTS = [
  {
    t: '14:32:08',
    kind: 'written-back',
    who: 'subarr → bazarr',
    duration: null,
    expanded: false,
    detail: 'Subtitle eng.srt written. Bazarr returned 200 · score=9.4',
    chips: [['HTTP', '200'], ['score', '9.4'], ['lang', 'eng']],
  },
  {
    t: '14:31:47',
    kind: 'scanned',
    who: 'subgen',
    duration: '22.3s',
    expanded: true,
    detail: 'whisper-large-v3 · 22.3s wall time · 12.4× realtime',
    kwargs: { model: 'whisper-large-v3', lang: 'en', fp16: true, vad: true, chunk: 30, hint_from_audio: true },
    output: ['842 segments · confidence 9.4', 'Severance.S02E08.eng.srt · 64 KB'],
  },
  {
    t: '14:30:12',
    kind: 'queued',
    who: 'subarr',
    duration: null,
    expanded: false,
    detail: 'Matched rule nightly_walk · priority 1 (Tautulli score 9.4)',
    chips: [['rule', 'nightly_walk'], ['priority', '1']],
  },
  {
    t: '14:30:11',
    kind: 'wanted',
    who: 'bazarr',
    duration: null,
    expanded: false,
    detail: 'Languages: eng · profile: default · provider chain: opensubs → addic7ed',
  },
  {
    t: '14:30:08',
    kind: 'probed',
    who: 'subarr',
    duration: '1.2s',
    expanded: false,
    detail: 'ffprobe · duration=58m12s · audio streams: 1 (eng, AC-3 5.1) · subtitle streams: 0',
    chips: [['audio', 'eng'], ['subs', '0'], ['runtime', '58:12']],
  },
  {
    t: '13:14:02',
    kind: 'discovered',
    who: 'subarr walk',
    duration: null,
    expanded: false,
    detail: 'Walk of /TV/Severance/Season 02/ · mtime=14:01 · sha=4f12…',
  },
];

const EVENT_COLOR = {
  'written-back': 'var(--success-500)',
  'scanned':      'var(--violet-500)',
  'queued':       'var(--cyan-500)',
  'wanted':       'var(--fg-1)',
  'probed':       'var(--fg-1)',
  'discovered':   'var(--fg-3)',
  'failed':       'var(--error-500)',
};

// ─── Background Activity surface ─────────────────────────────────
const BG_ACTIVITY = [
  { t: '14:32:08', kind: 'written-back', path: '/TV/Severance/Season 02/Severance.S02E08.mkv', meta: 'eng · 9.4',     focus: true },
  { t: '14:31:54', kind: 'scanned',      path: '/Movies/Furiosa (2024)/Furiosa.2024.mkv',      meta: 'subgen · 22s' },
  { t: '14:31:40', kind: 'queued',       path: '/TV/Shogun/Season 01/Shogun.S01E08.mkv',       meta: 'eng' },
  { t: '14:30:11', kind: 'probed',       path: '/TV/Andor/Season 01/Andor.S01E04.mkv',         meta: '2h12m · 5.1' },
  { t: '14:29:22', kind: 'failed',       path: '/Movies/Madame Web (2024)/Madame.Web.2024.mkv',meta: 'opensubs 429' },
  { t: '14:28:50', kind: 'written-back', path: '/TV/Fallout/Season 01/Fallout.S01E03.mkv',     meta: 'eng · 9.1' },
  { t: '14:28:33', kind: 'scanned',      path: '/TV/3 Body Problem/Season 01/3 Body Problem.S01E05.mkv', meta: 'subgen · 31s' },
  { t: '14:27:11', kind: 'probed',       path: '/TV/Ripley/Season 01/Ripley.S01E03.mkv',       meta: '54m · 5.1' },
  { t: '14:26:48', kind: 'queued',       path: '/Movies/Civil War (2024)/Civil.War.2024.mkv',  meta: 'eng' },
  { t: '14:25:02', kind: 'written-back', path: '/TV/Ripley/Season 01/Ripley.S01E02.mkv',       meta: 'eng,ita · 8.4' },
  { t: '14:24:18', kind: 'scanned',      path: '/Movies/Anora (2024)/Anora.2024.mkv',          meta: 'subgen · 19s' },
  { t: '14:23:55', kind: 'probed',       path: '/TV/Sugar/Season 01/Sugar.S01E04.mkv',         meta: '52m · 5.1' },
];

const KIND_DOT = {
  'written-back': 'ok',
  'scanned':      'violet',
  'queued':       'info',
  'probed':       'info',
  'failed':       'error',
};

function ActivityRow({ a }) {
  const dot = KIND_DOT[a.kind] || 'muted';
  return (
    <div className="act-row" style={{
      display: 'grid',
      gridTemplateColumns: '14px 72px 110px 1fr auto',
      alignItems: 'center', gap: 12,
      padding: '0 16px',
      height: 34,
      borderBottom: '1px solid var(--bg-3)',
      background: a.focus ? 'rgba(139,92,246,0.06)' : 'transparent',
      borderLeft: a.focus ? '2px solid var(--violet-500)' : '2px solid transparent',
      paddingLeft: a.focus ? 14 : 16,
      cursor: 'pointer',
    }}>
      <StatusDot kind={dot} />
      <span className="mono num" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>{a.t}</span>
      <span style={{ fontSize: 'var(--text-sm)', color: a.kind === 'failed' ? 'var(--error-500)' : 'var(--fg-1)', fontWeight: a.focus ? 600 : 500 }}>{a.kind}</span>
      <span className="mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-1)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.path}</span>
      <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: a.kind === 'failed' ? 'var(--error-500)' : 'var(--fg-2)' }}>{a.meta}</span>
    </div>
  );
}

function ActivityPageBody() {
  return (
    <main className="main-canvas" style={{ padding: '22px 24px 22px', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22, lineHeight: 1.15, fontWeight: 600, letterSpacing: '-0.005em' }}>Activity</h1>
          <div style={{ marginTop: 4, fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>
            Every probe, queue, scan, and write-back. Click any row for the per-file verdict.
          </div>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <button className="btn">Export NDJSON</button>
          <button className="btn">Pause</button>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="chip violet" style={{ height: 22, padding: '0 9px' }}>all</span>
        <span className="chip" style={{ height: 22, padding: '0 9px' }}>written-back</span>
        <span className="chip" style={{ height: 22, padding: '0 9px' }}>scanned</span>
        <span className="chip" style={{ height: 22, padding: '0 9px' }}>queued</span>
        <span className="chip" style={{ height: 22, padding: '0 9px' }}>probed</span>
        <span className="chip" style={{ height: 22, padding: '0 9px' }}>failed</span>
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }} className="num">last 24h · 4,128 events</span>
      </div>

      <div className="panel" style={{ flex: 1, minHeight: 0, padding: 0, overflow: 'auto' }}>
        {BG_ACTIVITY.map((a, i) => <ActivityRow key={i} a={a} />)}
      </div>
    </main>
  );
}

// ─── Modal: header ───────────────────────────────────────────────
function ModalHeader({ onClose }) {
  return (
    <div style={{
      padding: '16px 20px 14px',
      borderBottom: 'var(--border)',
      background: 'var(--bg-1)',
    }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 4 }}>
            <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: 'var(--fg-0)' }}>
              Severance · S02E08 · The After Hours
            </h2>
            <span className="chip ok" style={{ height: 18, padding: '0 7px' }}>9.4</span>
          </div>
          <div className="mono" style={{
            fontSize: 'var(--text-xs)',
            color: 'var(--fg-2)',
            lineHeight: 1.5,
            wordBreak: 'break-all',
          }}>/TV/Severance/Season 02/Severance.S02E08.1080p.WEB-DL.DDP5.1.x264-NTb.mkv</div>
          <div style={{ display: 'flex', gap: 14, marginTop: 8, fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
            <span><span style={{ color: 'var(--fg-3)' }}>size</span> <span className="mono num">4.2 GB</span></span>
            <span><span style={{ color: 'var(--fg-3)' }}>duration</span> <span className="mono num">58m 12s</span></span>
            <span><span style={{ color: 'var(--fg-3)' }}>audio</span> <span className="mono">eng · AC-3 5.1</span></span>
            <span><span style={{ color: 'var(--fg-3)' }}>last probed</span> <span className="mono">14:32:08</span></span>
          </div>
        </div>
        <button onClick={onClose} style={{
          color: 'var(--fg-2)',
          fontSize: 18, lineHeight: 1,
          width: 28, height: 28,
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          borderRadius: 4,
        }}
        onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-3)'}
        onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>×</button>
      </div>
    </div>
  );
}

// ─── Timeline event ──────────────────────────────────────────────
function TimelineEvent({ e, last }) {
  const color = EVENT_COLOR[e.kind] || 'var(--fg-2)';
  return (
    <div style={{ display: 'flex', gap: 14, position: 'relative' }}>
      {/* Dot column */}
      <div style={{ position: 'relative', flex: '0 0 14px', display: 'flex', justifyContent: 'center', paddingTop: 4 }}>
        <span style={{
          width: 10, height: 10,
          borderRadius: '50%',
          background: color,
          boxShadow: `0 0 0 3px ${e.kind === 'written-back' ? 'rgba(52,211,153,0.18)' : 'transparent'}`,
          zIndex: 1,
        }} />
      </div>
      {/* Body */}
      <div style={{ flex: 1, minWidth: 0, paddingBottom: last ? 0 : 20 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 4 }}>
          <span className="mono" style={{
            fontSize: 'var(--text-sm)',
            fontWeight: 600,
            color: 'var(--fg-0)',
            letterSpacing: '0.01em',
          }}>{e.kind}</span>
          <span className="mono num" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>{e.t}</span>
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>·</span>
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>{e.who}</span>
          {e.duration && (
            <span className="chip" style={{ height: 16, padding: '0 6px', fontSize: 'var(--text-2xs)' }}>{e.duration}</span>
          )}
          <span style={{ flex: 1 }} />
          {!e.expanded && <span style={{ color: 'var(--fg-3)', fontSize: 'var(--text-xs)' }}>▸</span>}
        </div>
        <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-1)', lineHeight: 1.5 }}>
          {e.detail}
        </div>
        {e.chips && (
          <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
            {e.chips.map(([k, v]) => (
              <span key={k} className="mono" style={{
                fontSize: 'var(--text-2xs)',
                color: 'var(--fg-2)',
                background: 'var(--bg-2)',
                border: '1px solid var(--bg-4)',
                borderRadius: 2,
                padding: '1px 6px',
              }}>
                <span style={{ color: 'var(--fg-3)' }}>{k}</span> {v}
              </span>
            ))}
          </div>
        )}
        {e.expanded && e.kwargs && (
          <div style={{ marginTop: 10 }}>
            <div style={{
              padding: '12px 14px',
              background: 'var(--bg-0)',
              border: '1px solid var(--bg-4)',
              borderRadius: 'var(--radius-md)',
              fontFamily: 'var(--font-mono)',
              fontSize: 'var(--text-xs)',
              color: 'var(--fg-1)',
              lineHeight: 1.65,
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                <span className="label">kwargs</span>
                <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)', textTransform: 'none', letterSpacing: 0 }}>passed verbatim to subgen</span>
              </div>
              {Object.entries(e.kwargs).map(([k, v]) => (
                <div key={k}>
                  <span style={{ color: 'var(--fg-2)' }}>{k}</span>
                  <span style={{ color: 'var(--fg-3)' }}>: </span>
                  <span style={{ color: typeof v === 'boolean' ? 'var(--cyan-500)' : typeof v === 'number' ? 'var(--cyan-500)' : 'var(--violet-400)' }}>
                    {typeof v === 'string' ? `"${v}"` : String(v)}
                  </span>
                </div>
              ))}
            </div>
            {e.output && (
              <div style={{ marginTop: 10 }}>
                <span className="label" style={{ display: 'block', marginBottom: 6 }}>output</span>
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 'var(--text-sm)', color: 'var(--fg-1)', lineHeight: 1.65 }}>
                  {e.output.map((o, i) => <li key={i} className="mono">{o}</li>)}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Timeline body ───────────────────────────────────────────────
function Timeline() {
  return (
    <div style={{
      flex: 1, minHeight: 0,
      padding: '18px 22px 18px 18px',
      overflow: 'auto',
      position: 'relative',
    }}>
      {/* Spine */}
      <div style={{
        position: 'absolute',
        left: 18 + 7, // padding-left + half dot
        top: 24,
        bottom: 24,
        width: 1,
        background: 'var(--bg-4)',
      }} />
      <div style={{ position: 'relative' }}>
        {FILE_EVENTS.map((e, i) => (
          <TimelineEvent key={i} e={e} last={i === FILE_EVENTS.length - 1} />
        ))}
      </div>
    </div>
  );
}

// ─── Modal footer ────────────────────────────────────────────────
function ModalFooter() {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10,
      padding: '12px 20px',
      background: 'var(--bg-1)',
      borderTop: 'var(--border)',
    }}>
      <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>
        ledger entry · <span className="mono">sub_4f12a8</span>
      </span>
      <span style={{ flex: 1 }} />
      <button className="btn ghost">View raw JSON</button>
      <button className="btn">Re-probe</button>
      <button className="btn">Re-scan</button>
      <button className="btn primary">Open in Bazarr ↗</button>
    </div>
  );
}

// ─── Modal shell ─────────────────────────────────────────────────
function FileModal({ onClose }) {
  return (
    <div style={{
      position: 'fixed', inset: 0,
      background: 'rgba(10,10,12,0.62)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      padding: 40,
      zIndex: 50,
      animation: 'fadein 160ms var(--ease-out)',
    }}
    onClick={onClose}>
      <div style={{
        width: 720,
        maxHeight: '88vh',
        background: 'var(--bg-1)',
        border: '1px solid var(--bg-4)',
        borderRadius: 'var(--radius-lg)',
        boxShadow: 'var(--shadow-modal)',
        display: 'flex', flexDirection: 'column',
        overflow: 'hidden',
      }}
      onClick={e => e.stopPropagation()}>
        <ModalHeader onClose={onClose} />
        <Timeline />
        <ModalFooter />
      </div>
    </div>
  );
}

// ─── Page wrapper ────────────────────────────────────────────────
export function FileModalPage() {
  const [open, setOpen] = useState(true);
  return (
    <>
      <ActivityPageBody />
      {open && <FileModal onClose={() => setOpen(false)} />}
      {!open && (
        <button onClick={() => setOpen(true)} style={{
          position: 'fixed', bottom: 16, right: 16,
          padding: '8px 14px',
          background: 'var(--violet-500)',
          color: '#fff',
          borderRadius: 'var(--radius-md)',
          fontSize: 'var(--text-sm)',
          fontWeight: 600,
          boxShadow: 'var(--shadow-modal)',
        }}>Re-open modal</button>
      )}
    </>
  );
}

