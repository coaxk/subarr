// Queue page — live read of subgen's active + pending scans.
//
// Reads /api/queue (5s poll). For v1.0 this is read-only: shows what
// subgen is currently working on, what's lined up behind it, plus a
// "send a manual scan" affordance so users can submit ad-hoc paths
// without going through Coverage/Library.
//
// Future (v1.0.x / v1.1): start/stop/remove/reorder controls. Those
// require new subgen endpoints (or careful use of /api/scan store
// state). Out of scope for v1.0 ship.

import { AsyncState, StatusDot, LibraryChip } from './atoms.jsx';

const { useState, useEffect, useCallback } = React;

// ─── Live data ───────────────────────────────────────────────────
function useLiveQueue(intervalMs = 5000) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchOnce = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setLoading(true);
    try {
      const r = await fetch('/api/queue', { credentials: 'same-origin' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json()); setError(null);
    } catch (e) {
      setError((prev) => (data ? prev : e));
      // eslint-disable-next-line no-console
      console.debug('queue fetch failed:', e);
    } finally {
      if (!silent) setLoading(false);
    }
  }, [data]);

  useEffect(() => {
    let cancelled = false; let timer = null;
    async function loop() {
      if (cancelled) return;
      await fetchOnce({ silent: true });
      if (!cancelled) timer = setTimeout(loop, intervalMs);
    }
    (async () => { await fetchOnce(); if (!cancelled) timer = setTimeout(loop, intervalMs); })();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs]);

  return { data, loading, error, refetch: fetchOnce };
}

// ─── Row primitives ──────────────────────────────────────────────
function fmtPct(n) {
  if (n == null) return '—';
  if (typeof n !== 'number') n = parseFloat(n);
  if (!isFinite(n)) return '—';
  return `${Math.round(n)}%`;
}

function fmtDuration(secs) {
  if (secs == null || !isFinite(secs)) return '—';
  if (secs < 60) return `${Math.round(secs)}s`;
  if (secs < 3600) return `${Math.floor(secs/60)}m ${Math.floor(secs%60)}s`;
  return `${Math.floor(secs/3600)}h ${Math.floor((secs%3600)/60)}m`;
}

function ProgressBar({ pct, width = 220 }) {
  const value = Math.max(0, Math.min(100, pct || 0));
  // Gradient ramp — green → cyan → violet → pink, matches original
  // subarr's "alive" feel. Use fixed full-width gradient and reveal it
  // via width%, so colour position stays anchored to absolute progress.
  return (
    <div style={{
      width, height: 8,
      background: 'var(--bg-3)',
      borderRadius: 4, overflow: 'hidden',
      position: 'relative',
    }}>
      <div style={{
        position: 'absolute', top: 0, left: 0, bottom: 0,
        width: `${value}%`,
        background: 'linear-gradient(90deg, #22d3a1 0%, #38bdf8 35%, #8b5cf6 70%, #ec4899 100%)',
        backgroundSize: `${width}px 100%`,
        backgroundRepeat: 'no-repeat',
        transition: 'width var(--dur-base) var(--ease-out)',
        boxShadow: '0 0 8px rgba(139, 92, 246, 0.35)',
      }} />
    </div>
  );
}

function QueueRow({ item, kind, canCancel, onCancel, busy }) {
  // Subgen's processing entries (via subarr-next proxy) look like:
  // { path, type, progress: { pct, cur_s, tot_s, elapsed, eta, speed_s_per_s } }
  // Queued entries are bare { path, type, position? } — no progress block.
  const path = item.path || item.canonical_path || item.file || '';
  const stage = item.type || (kind === 'processing' ? 'transcribing' : 'queued');
  const prog = item.progress || {};
  const pct = prog.pct ?? null;
  const elapsed = prog.elapsed || null;
  const eta = prog.eta || null;
  const speed = prog.speed_s_per_s != null ? `${prog.speed_s_per_s.toFixed(1)}×` : null;
  const cur = prog.cur_s != null && prog.tot_s != null
    ? `${fmtClock(prog.cur_s)} / ${fmtClock(prog.tot_s)}`
    : null;

  return (
    <div data-testid="queue-row" data-queue-kind={kind} style={{
      display: 'flex', flexDirection: 'column',
      // Row body indented further than the section header (which uses
      // row-cozy = 16px horizontal) so rows read as children of their
      // section. Otherwise the path + dot slam against the panel edge
      // at the same x as the section title.
      padding: '10px 24px 10px 32px',
      borderBottom: '1px solid var(--bg-3)',
      gap: 4,
    }}>
      {/* Row 1: status + path + stage */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span title={kind === 'processing' ? 'Subgen is currently transcribing this file' : 'Queued — waiting for subgen to start'}>
          <StatusDot kind={kind === 'processing' ? 'violet' : 'info'} pulse={kind === 'processing'} />
        </span>
        <span className="mono" title={path} style={{
          flex: 1,
          fontSize: 'var(--text-xs)',
          color: 'var(--fg-1)',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          minWidth: 0,
        }}>{path}</span>
        <span className="mono"
          title="Current subgen processing stage (transcribing / queued / writing-srt)"
          style={{
            fontSize: 'var(--text-2xs)',
            color: 'var(--fg-2)',
            letterSpacing: '0.04em',
            textTransform: 'uppercase',
            cursor: 'help',
          }}>{stage}</span>
        {/* #58 v4.4: cancel button on queued rows only. Processing rows
            can't be safely killed mid-flight (would orphan the worker). */}
        {kind === 'queued' && canCancel && (
          <button className="btn ghost sm"
            onClick={() => onCancel && onCancel(path)}
            disabled={busy}
            title="Remove this task from the subgen queue before it starts"
            aria-label={`Cancel queued task ${path}`}>
            {busy ? '…' : '✕ cancel'}
          </button>
        )}
        {kind === 'queued' && !canCancel && (
          <span className="mono"
            title="Queue-cancel requires subarr-subgen v4.4+. Update the subgen container to enable cancel."
            style={{
              fontSize: 'var(--text-2xs)', color: 'var(--fg-3)',
              cursor: 'help', opacity: 0.6,
            }}>cancel: needs v4.4+</span>
        )}
      </div>

      {/* Row 2 (processing only): progress bar + numbers */}
      {kind === 'processing' && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div data-testid="queue-progress" style={{ flex: 1 }}>
            <ProgressBar pct={pct} />
          </div>
          <span className="num mono" style={{
            fontSize: 'var(--text-xs)',
            color: 'var(--fg-0)',
            fontWeight: 600,
            minWidth: 42,
            textAlign: 'right',
          }}>{pct != null ? fmtPct(pct) : '—'}</span>
        </div>
      )}

      {/* Row 3 (processing only): elapsed · eta · speed · current/total seconds */}
      {kind === 'processing' && (
        <div style={{ display: 'flex', gap: 16, fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>
          {cur && (
            <span title="How far into the audio subgen has transcribed (current / total)"><span style={{ color: 'var(--fg-3)', cursor: 'help' }}>position</span> <span className="mono" style={{ color: 'var(--fg-2)' }}>{cur}</span></span>
          )}
          {elapsed && (
            <span title="Wall-clock time since subgen started transcribing this file"><span style={{ color: 'var(--fg-3)', cursor: 'help' }}>elapsed</span> <span className="mono" style={{ color: 'var(--fg-2)' }}>{elapsed}</span></span>
          )}
          {eta && (
            <span title="Estimated time remaining at current speed"><span style={{ color: 'var(--fg-3)', cursor: 'help' }}>eta</span> <span className="mono" style={{ color: 'var(--fg-2)' }}>{eta}</span></span>
          )}
          {speed && (
            <span title="Transcribe rate vs realtime — 1.0× means same speed as the audio, 5× means 5 minutes of audio per minute of wall time"><span style={{ color: 'var(--fg-3)', cursor: 'help' }}>speed</span> <span className="mono" style={{ color: 'var(--cyan-500)' }}>{speed} realtime</span></span>
          )}
        </div>
      )}
    </div>
  );
}

// ─── History row (v1.1.1 Featured Queue) ─────────────────────────
// One row per scan_store submission outcome (per-path). Renders the
// outcome chip + reason + actions. Issues vs Recently-done sections
// both use this — `compact` mode shrinks the row for the done list.
const OUTCOME_STYLE = {
  ok:       { fg: 'var(--success-500)', bg: 'rgba(52,211,153,0.10)',  br: 'rgba(52,211,153,0.35)', label: 'queued' },
  running:  { fg: 'var(--violet-400)',  bg: 'rgba(139,92,246,0.10)',  br: 'rgba(139,92,246,0.35)', label: 'running' },
  skipped:  { fg: 'var(--warn-500)',    bg: 'rgba(245,158,11,0.10)',  br: 'rgba(245,158,11,0.35)', label: 'skipped' },
  // skip_reason='sub_exists' — matching .srt already on disk. Not really
  // an issue: subgen had nothing to do, the file is effectively "done".
  // Render in neutral grey so it doesn't blend into the warning rows;
  // routed to Recently-done instead of Issues.
  sub_exists: { fg: 'var(--fg-2)',      bg: 'var(--bg-2)',            br: 'var(--bg-4)',           label: 'sub already exists' },
  error:    { fg: 'var(--error-500)',   bg: 'rgba(239,68,68,0.10)',   br: 'rgba(239,68,68,0.35)',  label: 'failed' },
  // #229: subgen restarted before transcription completed. Distinct
  // colour from 'failed' so it doesn't read like a real error — it's
  // just lost work that should be requeued, not a broken file.
  orphaned: { fg: 'var(--warn-500)',    bg: 'rgba(245,158,11,0.18)',  br: 'rgba(245,158,11,0.55)', label: 'lost on restart' },
  pending:  { fg: 'var(--fg-2)',        bg: 'var(--bg-2)',            br: 'var(--bg-4)',           label: 'pending' },
};

function OutcomeChip({ category, label }) {
  const v = OUTCOME_STYLE[category] || OUTCOME_STYLE.pending;
  return (
    <span className="mono" style={{
      display: 'inline-block', padding: '1px 7px',
      borderRadius: 2,
      border: `1px solid ${v.br}`, color: v.fg, background: v.bg,
      fontSize: 'var(--text-2xs)', lineHeight: '15px',
      letterSpacing: '0.01em', whiteSpace: 'nowrap',
    }}>{label || v.label}</span>
  );
}

// Small theme-matched checkbox for queue bulk-select.
function QCheck({ checked, onChange, indeterminate }) {
  return (
    <span
      onClick={(e) => { e.stopPropagation(); onChange(); }}
      role="checkbox" aria-checked={checked}
      style={{
        width: 14, height: 14, flex: '0 0 14px', borderRadius: 3, cursor: 'pointer',
        border: `1.5px solid ${checked || indeterminate ? 'var(--violet-500)' : 'var(--bg-5)'}`,
        background: checked || indeterminate ? 'var(--violet-500)' : 'transparent',
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        color: '#fff', fontSize: 10, lineHeight: 1,
      }}>
      {checked ? '✓' : (indeterminate ? '–' : '')}
    </span>
  );
}

function HistoryRow({ entry, onRequeue, onRemove, busy, checked, onToggleSel }) {
  const path = entry.path;
  const out = entry.outcome || {};
  // For skipped rows where queue.py heuristically identified the
  // matching .srt on disk, swap to a distinct chip style ('sub_exists').
  // The underlying outcome.category stays 'skipped' for API consumers;
  // only the visual category changes.
  const rawCategory = out.category || 'pending';
  // Benign skips (matching .srt on disk, or the file was removed) get the
  // neutral 'sub_exists' chip style rather than the warn-amber 'skipped' one.
  const benignSkip = out.skip_reason === 'sub_exists' || out.skip_reason === 'file_removed'
                  || out.skip_reason === 'interrupted';
  const category = (rawCategory === 'skipped' && benignSkip)
    ? 'sub_exists'
    : rawCategory;
  const ageS = Math.max(0, (Date.now() / 1000) - (entry.created_at || 0));
  const ageLabel = ageS < 60 ? `${Math.round(ageS)}s ago`
                  : ageS < 3600 ? `${Math.floor(ageS/60)}m ago`
                  : ageS < 86400 ? `${Math.floor(ageS/3600)}h ago`
                  : `${Math.floor(ageS/86400)}d ago`;
  // Requeue only makes sense for terminal outcomes (skipped/error/done).
  // For currently-running rows the action is hidden.
  const canRequeue = category === 'skipped' || category === 'error'
                  || category === 'ok' || category === 'orphaned'
                  || category === 'sub_exists';
  return (
    <div style={{
      display: 'flex', flexDirection: 'column',
      // Match the indent on Processing/Queued rows so all 4 sections
      // visually align under their respective section headers.
      padding: '10px 24px 10px 32px',
      borderBottom: '1px solid var(--bg-3)',
      gap: 4,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        {onToggleSel && <QCheck checked={!!checked} onChange={onToggleSel} />}
        <OutcomeChip category={category} label={out.label} />
        <span className="mono" title={path} style={{
          flex: 1, fontSize: 'var(--text-xs)', color: 'var(--fg-1)',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          minWidth: 0,
        }}>{path}</span>
        <LibraryChip library={entry.library} />
        <span className="mono" style={{
          fontSize: 'var(--text-2xs)', color: 'var(--fg-3)',
        }}>{ageLabel}</span>
        {canRequeue && (
          <button className="btn ghost sm"
            onClick={() => onRequeue && onRequeue(path, entry.scan_id)}
            disabled={busy}
            title="Resubmit this path to subgen as a new scan (removes this entry)"
            aria-label={`Requeue ${path}`}>
            {busy ? '…' : '↻ requeue'}
          </button>
        )}
        <button className="btn ghost sm"
          onClick={() => onRemove && onRemove(entry.scan_id)}
          disabled={busy}
          title="Remove this entry from history (does not affect live subgen queue)"
          aria-label={`Remove scan ${entry.scan_id} from history`}>
          ✕
        </button>
      </div>
      {out.detail && (
        <div style={{
          paddingLeft: 4,
          fontSize: 'var(--text-2xs)', color: 'var(--fg-2)',
          fontStyle: 'italic',
        }}>{out.detail}</div>
      )}
    </div>
  );
}

// Format seconds as MM:SS or HH:MM:SS.
function fmtClock(s) {
  if (s == null) return '—';
  const total = Math.floor(s);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
  return `${m}:${String(sec).padStart(2,'0')}`;
}

// ─── Manual submit form ──────────────────────────────────────────
function SubmitScanForm({ onSubmitted }) {
  const [path, setPath] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    const trimmed = path.trim();
    if (!trimmed) return;
    setBusy(true); setError(null);
    try {
      const r = await fetch('/api/scan', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths: [trimmed], reverse: false }),
      });
      if (!r.ok && r.status !== 202) {
        const text = await r.text().catch(() => '');
        throw new Error(`HTTP ${r.status}: ${text.slice(0, 200)}`);
      }
      setPath(''); onSubmitted && onSubmitted();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} style={{
      display: 'flex', gap: 10, alignItems: 'flex-start',
    }}>
      <div style={{ flex: 1 }}>
        <input
          type="text"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          placeholder='Canonical path, e.g. "TV/Severance/Season 02/Severance.S02E08.mkv"'
          style={{
            width: '100%', height: 32, padding: '0 12px',
            background: 'var(--bg-2)',
            border: '1px solid var(--bg-4)',
            borderRadius: 'var(--radius-md)',
            fontFamily: 'var(--font-mono)',
            fontSize: 'var(--text-sm)',
            color: 'var(--fg-0)',
          }} />
        {error && (
          <div style={{ marginTop: 6, fontSize: 'var(--text-xs)', color: 'var(--error-500)' }}>
            {String(error.message || error)}
          </div>
        )}
        <div style={{ marginTop: 4, fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>
          Path is relative to the library root. Files and directories are both valid.
        </div>
      </div>
      <button className="btn primary" type="submit" disabled={busy || !path.trim()}>
        {busy ? 'Submitting…' : 'Submit scan'}
      </button>
    </form>
  );
}

// #336: the manual-scan-by-path form is a power-user escape hatch (a path subarr
// doesn't surface as a gap). The everyday way to queue is the Gaps page or the
// Library tree (browse + select), so tuck it behind a collapsed "Advanced"
// disclosure to keep the Queue page focused on monitoring.
function AdvancedScanDisclosure({ onSubmitted }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="panel" style={{ padding: '10px 18px' }}>
      <div
        onClick={() => setOpen(o => !o)}
        style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}
      >
        <span style={{ color: 'var(--fg-3)', fontSize: 'var(--text-xs)' }}>{open ? '▾' : '▸'}</span>
        <span className="label">Advanced</span>
        <span style={{ color: 'var(--fg-3)', fontSize: 'var(--text-xs)' }}>submit a manual scan by path</span>
      </div>
      {open && (
        <div style={{ marginTop: 12 }}>
          <div style={{ color: 'var(--fg-2)', fontSize: 'var(--text-xs)', marginBottom: 8 }}>
            For a path subarr doesn't surface as a gap. Most of the time, queue from the{' '}
            <a href="/coverage">Gaps</a> page or the <a href="/library">Library</a> tree instead.
          </div>
          <SubmitScanForm onSubmitted={onSubmitted} />
        </div>
      )}
    </div>
  );
}

// ─── #66/#116: pending-queue authority (subarr's backlog before subgen) ──
function useLivePending(intervalMs = 5000) {
  const [data, setData] = useState(null);
  const fetchOnce = useCallback(async () => {
    try {
      const r = await fetch('/api/queue/pending', { credentials: 'same-origin' });
      if (r.ok) setData(await r.json());
    } catch (e) { /* keep last-good */ }
  }, []);
  useEffect(() => {
    let cancelled = false; let timer = null;
    const loop = async () => {
      if (cancelled) return;
      await fetchOnce();
      if (!cancelled) timer = setTimeout(loop, intervalMs);
    };
    loop();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [intervalMs, fetchOnce]);
  return { data, refetch: fetchOnce };
}

const SOURCE_STYLE = {
  manual:   { fg: '#facc15', label: 'manual' },
  gaps:     { fg: '#4ade80', label: 'gaps' },
  auto:     { fg: '#38bdf8', label: 'auto' },
  backfill: { fg: '#94a3b8', label: 'backfill' },
};

function PendingControls({ paused, targetDepth, onToggle, onDepth }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
      <button className={`btn ${paused ? 'violet' : ''}`} onClick={onToggle}
        title={paused ? 'Resume feeding subgen' : 'Pause — stop sending new jobs to subgen (in-flight keep running)'}
        style={{ fontSize: 'var(--text-xs)' }}>
        {paused ? '▶ Resume feed' : '⏸ Pause feed'}
      </button>
      <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}
        title="How many jobs subarr keeps subgen working on at once (queued+processing). The rest wait here, reorderable.">
        target depth
        <button className="btn" style={{ padding: '0 8px' }}
          onClick={() => onDepth(Math.max(0, (targetDepth || 0) - 1))}>−</button>
        <span className="mono" style={{ minWidth: 14, textAlign: 'center' }}>{targetDepth}</span>
        <button className="btn" style={{ padding: '0 8px' }}
          onClick={() => onDepth((targetDepth || 0) + 1)}>+</button>
      </span>
    </div>
  );
}

function PendingRow({ job, idx, total, onMoveUp, onMoveDown, onRemove }) {
  const src = SOURCE_STYLE[job.source] || { fg: 'var(--fg-3)', label: job.source };
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10, padding: '9px 18px',
      borderTop: idx === 0 ? 'none' : 'var(--border)',
    }}>
      <span className="mono" style={{ width: 22, textAlign: 'right', color: 'var(--fg-3)', fontSize: 'var(--text-xs)' }}>
        {idx + 1}
      </span>
      <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
        title={job.canonical_path}>
        {job.canonical_path}
      </span>
      {job.audio_language_override && (
        <span className="chip" style={{ fontSize: 'var(--text-2xs)' }} title="Audio-language override sent to subgen">
          {job.audio_language_override}
        </span>
      )}
      <span style={{ fontSize: 'var(--text-2xs)', color: src.fg, width: 56, textAlign: 'right' }}>{src.label}</span>
      <span style={{ display: 'flex', gap: 4, flex: 'none' }}>
        <button className="btn" title="Move up" disabled={idx === 0}
          onClick={() => onMoveUp(job.id)} style={{ padding: '0 7px' }}>↑</button>
        <button className="btn" title="Move down" disabled={idx === total - 1}
          onClick={() => onMoveDown(job.id)} style={{ padding: '0 7px' }}>↓</button>
        <button className="btn" title="Remove from queue"
          onClick={() => onRemove(job.id)} style={{ padding: '0 7px' }}>✕</button>
      </span>
    </div>
  );
}

function PendingPanel() {
  const { data, refetch } = useLivePending();
  const pending = (data && data.pending) || [];
  const paused = !!(data && data.paused);
  const targetDepth = data ? data.target_depth : 2;

  const control = useCallback(async (body) => {
    try {
      await fetch('/api/queue/control', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      refetch();
    } catch (e) { /* best-effort */ }
  }, [refetch]);

  const act = useCallback(async (id, verb) => {
    try {
      const opts = { method: verb === 'remove' ? 'DELETE' : 'POST', credentials: 'same-origin' };
      const url = verb === 'remove'
        ? `/api/queue/pending/${id}`
        : `/api/queue/pending/${id}/${verb}`;
      await fetch(url, opts);
      refetch();
    } catch (e) { /* best-effort */ }
  }, [refetch]);

  // #169/#336: step-wise reorder. The backend resolves the adjacent row from
  // CURRENT state (up = before the row above, down = after the row below) and
  // moves one step, adopting the target's priority so up/down cross priority
  // buckets one step at a time. We just say "up"/"down" — passing a before_id/
  // after_id computed from this (poll-lagged) list targeted stale rows once the
  // feeder churned the queue, so arrows would no-op or stop after a few clicks.
  const moveStep = useCallback(async (id, dir) => {
    try {
      await fetch(`/api/queue/pending/${id}/move-step`, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ dir }),
      });
      refetch();
    } catch (e) { /* best-effort */ }
  }, [refetch]);

  // Hide the whole panel until we know there's something to manage OR the feed
  // is paused — keeps the page clean for users who never build a backlog.
  if (data && pending.length === 0 && !paused) return null;

  return (
    <div className="panel" style={{ padding: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden', flexShrink: 0 }}>
      <div style={{ padding: '12px 18px', borderBottom: 'var(--border)', display: 'flex', alignItems: 'center', gap: 10 }}>
        <span className="label" title="subarr's own backlog. Jobs land here first, then subarr feeds them into subgen's Queued above at your target depth instead of flooding the GPU.">Pending <span style={{ fontWeight: 400, color: 'var(--fg-3)' }}>· subarr backlog</span></span>
        <span className="num mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>{pending.length}</span>
        {paused && (
          <span className="chip" style={{ fontSize: 'var(--text-2xs)', color: '#facc15' }}>feed paused</span>
        )}
        <span style={{ flex: 1 }} />
        <PendingControls
          paused={paused} targetDepth={targetDepth}
          onToggle={() => control({ paused: !paused })}
          onDepth={(v) => control({ target_depth: v })}
        />
      </div>
      <div style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)', padding: '6px 18px', borderBottom: 'var(--border)' }}>
        subarr's backlog — fed into subgen as it frees up (target depth {targetDepth}). Reorder or remove before they're sent.
      </div>
      <div>
        {pending.length === 0
          ? <div style={{ padding: '14px 18px', color: 'var(--fg-3)', fontSize: 'var(--text-sm)' }}>
              Feed paused — nothing waiting. New jobs will hold here until you resume.
            </div>
          : pending.map((job, i) => (
              <PendingRow key={job.id} job={job} idx={i} total={pending.length}
                onMoveUp={(id) => moveStep(id, 'up')}
                onMoveDown={(id) => moveStep(id, 'down')}
                onRemove={(id) => act(id, 'remove')} />
            ))}
      </div>
    </div>
  );
}

// #116: load the whole verified-gap backlog into the pending queue; the feeder
// drains it gently at target_depth (no GPU stampede).
function BackfillButton({ onDone }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const run = useCallback(async () => {
    setBusy(true); setMsg(null);
    try {
      const r = await fetch('/api/queue/backfill', { method: 'POST', credentials: 'same-origin' });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) setMsg(d.detail || `HTTP ${r.status}`);
      else { setMsg(`+${d.enqueued} queued · ${d.pending_total} pending`); onDone && onDone(); }
    } catch (e) {
      setMsg('backfill failed');
    } finally {
      setBusy(false);
      setTimeout(() => setMsg(null), 7000);
    }
  }, [onDone]);
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
      {msg && <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>{msg}</span>}
      <button className="btn" disabled={busy} onClick={run}
        title="Load every verified subtitle gap into the pending queue. The feeder drains them gently in the background (target depth), so a big backlog closes without flooding the GPU.">
        {busy ? 'Backfilling…' : 'Backfill gaps'}
      </button>
    </span>
  );
}

// ─── Arena slot indicator ─────────────────────────────────────────
// Exported helper — returns the label string when a sweep is in-flight,
// or null when none. Tests assert on this directly (Node env, no rendering).
export function arenaSlotText(arenaActive) {
  if (!arenaActive) return null;
  return `Tuning Lab sweep running · using ${arenaActive} GPU slot${arenaActive === 1 ? '' : 's'}`;
}

export function ArenaSlotIndicator({ arenaActive }) {
  const text = arenaSlotText(arenaActive);
  if (!text) return null;
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px',
      background: 'var(--bg-1)', border: '1px solid var(--bg-3)',
      borderRadius: 'var(--radius-md)', color: 'var(--fg-2)', fontSize: 'var(--text-sm)',
    }}>
      <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--violet-500)', flex: 'none' }} />
      {text}
    </div>
  );
}

// ─── Page ────────────────────────────────────────────────────────

// [#469] Pending rows whose English coverage is already satisfied on disk.
//
// #468 stops new ones accumulating by acting on subgen's skip verdict straight
// away. This surfaces the ones that piled up before that landed: each holds a
// feeder depth slot for 30s as it drains, so a backlog costs real GPU idle time.
//
// Deletes nothing without a second click, and shows exactly which rows first.
// The apply side deliberately never touches a bypass_skip row or a PENDING one.
function SatisfiedPendingBanner({ onResolved }) {
  const [state, setState] = useState(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await fetch('/api/admin/db/pending-satisfied', { credentials: 'same-origin' });
        if (!r.ok) return;              // silent: a nicety, not core UI
        const body = await r.json();
        if (alive) setState(body);
      } catch { /* offline or blocked: stay quiet rather than alarm */ }
    })();
    return () => { alive = false; };
  }, []);

  const apply = async () => {
    setBusy(true);
    try {
      const r = await fetch('/api/admin/db/pending-satisfied/reconcile', {
        method: 'POST', credentials: 'same-origin',
      });
      setDone(await r.json());
      // Re-read rather than trusting the POST echo -- believing the thing under
      // test is how you ship a cleanup that reports success and removes nothing.
      const again = await fetch('/api/admin/db/pending-satisfied', { credentials: 'same-origin' });
      if (again.ok) setState(await again.json());
      if (onResolved) onResolved();
    } catch (e) {
      setDone({ resolved: 0, error: e.message || String(e) });
    } finally {
      setBusy(false);
    }
  };

  if (dismissed || !state) return null;
  const n = (state.satisfied || []).length;
  if (!n && !done) return null;

  return (
    <div style={{
      border: '1px solid var(--border, #333)', borderRadius: 8,
      padding: '10px 12px', fontSize: 'var(--text-sm)',
      display: 'flex', flexDirection: 'column', gap: 8,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{ flex: 1 }}>
          {done ? (
            <span>
              {done.error
                ? `Cleanup failed: ${done.error}`
                : `Removed ${done.resolved} stale queue row${done.resolved === 1 ? '' : 's'}.`}
            </span>
          ) : (
            <span>
              <strong>{n} queued job{n === 1 ? '' : 's'} already have English subtitles on disk.</strong>{' '}
              subgen will refuse each one, and every refusal holds a slot for 30 seconds
              before the queue moves on.
            </span>
          )}
        </div>
        {!done && (
          <button className="btn" type="button" onClick={() => setOpen(v => !v)}>
            {open ? 'Hide' : 'Show these'}
          </button>
        )}
        <button className="btn" type="button" onClick={() => setDismissed(true)} aria-label="Dismiss">
          {'×'}
        </button>
      </div>

      {open && !done && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{
            maxHeight: 180, overflowY: 'auto', fontSize: 'var(--text-xs)',
            color: 'var(--fg-2)', border: '1px solid var(--border, #333)',
            borderRadius: 6, padding: 8,
          }}>
            {(state.satisfied || []).map(p => <div key={p}>{p}</div>)}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <button className="btn" type="button" onClick={apply} disabled={busy}>
              {busy ? 'Removing...' : `Remove ${n} stale row${n === 1 ? '' : 's'}`}
            </button>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
              Removes queue entries only. No subtitle or media file is touched, and
              jobs you asked to transcribe anyway are left alone.
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

export function QueuePage() {
  const { data, loading, error, refetch } = useLiveQueue();
  const [busyAction, setBusyAction] = useState(null);  // scan_id or 'clear'
  // Bulk-select across the history sections (Lost-on-restart / Issues /
  // Recently done). Keyed by scan_id::path so requeue (by path) and remove
  // (by scan_id) both work from one selection.
  const [selRows, setSelRows] = useState(() => new Map());
  const [bulkBusy, setBulkBusy] = useState(null);  // 'requeue' | 'remove' | null
  const isSel = useCallback((e) => selRows.has(`${e.scan_id}::${e.path}`), [selRows]);
  const toggleSel = useCallback((e) => {
    setSelRows(prev => {
      const n = new Map(prev); const k = `${e.scan_id}::${e.path}`;
      if (n.has(k)) n.delete(k); else n.set(k, { path: e.path, scanId: e.scan_id });
      return n;
    });
  }, []);
  const setSectionSel = useCallback((entries, on) => {
    setSelRows(prev => {
      const n = new Map(prev);
      for (const e of entries) {
        const k = `${e.scan_id}::${e.path}`;
        if (on) n.set(k, { path: e.path, scanId: e.scan_id }); else n.delete(k);
      }
      return n;
    });
  }, []);
  const clearSel = useCallback(() => setSelRows(new Map()), []);

  const processing = data?.processing || [];
  const queued = data?.queued || [];
  const idle = data?.idle === true;
  const subgenVersion = data?.version;
  // v1.1.1: Featured Queue history. Split into Issues (skipped/error),
  // Lost on restart (orphaned — #229), and Recently done (ok/running)
  // so the eye-catching problem rows are top.
  const history = data?.history || [];
  const orphaned = history.filter(h => h.outcome?.category === 'orphaned');
  // Benign skips (matching .srt already on disk, or the file was removed
  // before subgen ran) are NOT issues — route them into Recently done.
  // Everything else skipped (unknown reason, likely audio_lang) stays in
  // Issues so the user notices and can verify their skip-language list.
  const isBenignSkip = (o) => o.skip_reason === 'sub_exists' || o.skip_reason === 'file_removed'
                          || o.skip_reason === 'interrupted';
  const issues = history.filter(h => {
    const o = h.outcome || {};
    if (o.category === 'error') return true;
    if (o.category === 'skipped' && !isBenignSkip(o)) return true;
    return false;
  });
  const completed = history.filter(h => {
    const o = h.outcome || {};
    if (o.category === 'ok' || o.category === 'running') return true;
    if (o.category === 'skipped' && isBenignSkip(o)) return true;
    return false;
  });
  const completedShown = completed.slice(0, 100);  // section caps display at 100
  const counts = data?.history_counts || {};
  // #58 v4.4: subgen advertises capabilities.queue_cancel via /queue.
  // When present, render the cancel button on queued rows; when absent
  // show the "needs v4.4+" hint so the user knows to upgrade.
  const canCancel = data?.capabilities?.queue_cancel === true;

  const requeue = useCallback(async (path, scanId) => {
    setBusyAction(scanId || path);
    try {
      const r = await fetch('/api/queue/requeue', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
      });
      if (!r.ok && r.status !== 202) {
        const t = await r.text().catch(() => '');
        throw new Error(`HTTP ${r.status}: ${t.slice(0, 200)}`);
      }
      // Requeued from a history row → drop the old failed/cancelled entry so it
      // doesn't linger in Issues; the new attempt creates its own history row.
      if (scanId) {
        await fetch(`/api/queue/scan/${scanId}`, { method: 'DELETE', credentials: 'same-origin' }).catch(() => {});
      }
      await refetch({ silent: true });
    } catch (e) {
      alert(`Requeue failed: ${e.message}`);
    } finally { setBusyAction(null); }
  }, [refetch]);

  // #58 v4.4: cancel a queued (not yet processing) subgen task by path.
  // Backend returns 503 if subgen doesn't advertise queue_cancel — the
  // UI gates the button so this is a defence-in-depth check only.
  const cancelQueued = useCallback(async (path) => {
    setBusyAction(`cancel:${path}`);
    try {
      const r = await fetch('/api/queue/cancel', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
      });
      if (!r.ok) {
        const t = await r.text().catch(() => '');
        throw new Error(`HTTP ${r.status}: ${t.slice(0, 200)}`);
      }
      const body = await r.json().catch(() => ({}));
      if (body && body.cancelled === false && body.reason) {
        alert(`Cancel skipped: ${body.reason}`);
      }
      await refetch({ silent: true });
    } catch (e) {
      alert(`Cancel failed: ${e.message}`);
    } finally { setBusyAction(null); }
  }, [refetch]);

  const removeOne = useCallback(async (scanId) => {
    setBusyAction(scanId);
    try {
      const r = await fetch(`/api/queue/scan/${scanId}`, {
        method: 'DELETE', credentials: 'same-origin',
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await refetch({ silent: true });
    } catch (e) {
      alert(`Remove failed: ${e.message}`);
    } finally { setBusyAction(null); }
  }, [refetch]);

  // Bulk actions over the current selection. Serial (Bazarr/Sonarr are
  // rate-limited upstream), one refetch at the end, then clear selection.
  const bulkRequeue = useCallback(async () => {
    const targets = [...selRows.values()];
    if (!targets.length) return;
    setBulkBusy('requeue');
    let errs = 0;
    for (const t of targets) {
      try {
        const r = await fetch('/api/queue/requeue', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: t.path }),
        });
        if (!r.ok && r.status !== 202) errs += 1;
      } catch { errs += 1; }
    }
    setBulkBusy(null); clearSel();
    if (errs) alert(`${errs} of ${targets.length} requeue(s) failed`);
    await refetch({ silent: true });
  }, [selRows, refetch, clearSel]);

  const bulkRemove = useCallback(async () => {
    const targets = [...selRows.values()];
    if (!targets.length) return;
    if (!window.confirm(`Remove ${targets.length} entr${targets.length === 1 ? 'y' : 'ies'} from history?`)) return;
    setBulkBusy('remove');
    let errs = 0;
    for (const t of targets) {
      try {
        const r = await fetch(`/api/queue/scan/${t.scanId}`, {
          method: 'DELETE', credentials: 'same-origin',
        });
        if (!r.ok) errs += 1;
      } catch { errs += 1; }
    }
    setBulkBusy(null); clearSel();
    if (errs) alert(`${errs} of ${targets.length} remove(s) failed`);
    await refetch({ silent: true });
  }, [selRows, refetch, clearSel]);

  const clearByStatus = useCallback(async (statuses, label) => {
    if (!window.confirm(`Clear all ${label} from history? This can't be undone.`)) return;
    setBusyAction('clear');
    try {
      const r = await fetch('/api/queue/clear', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ statuses }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await refetch({ silent: true });
    } catch (e) {
      alert(`Clear failed: ${e.message}`);
    } finally { setBusyAction(null); }
  }, [refetch]);

  const isInitialLoad = loading && !data;
  const isError = error && !data;
  const isEmpty = !isInitialLoad && !isError && processing.length === 0 && queued.length === 0;

  return (
    <main className="main-canvas" style={{ padding: '22px 24px 22px', gap: 16, overflow: 'auto' }}>
      <SatisfiedPendingBanner onResolved={refetch} />
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 'var(--text-h1)', lineHeight: 'var(--lh-h1)', fontWeight: 600 }}>Queue</h1>
          <div style={{ marginTop: 4, fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>
            Live view of what subgen is currently transcribing and what's waiting in line.
            {subgenVersion && <span> Subgen <span className="mono">v{subgenVersion}</span>.</span>}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>
            {idle ? (
              <span><StatusDot kind="muted" /> idle</span>
            ) : (
              <span><StatusDot kind="info" pulse /> active</span>
            )}
          </span>
          <BackfillButton onDone={() => refetch()} />
          <button className="btn" onClick={() => refetch()}>Refresh</button>
        </div>
      </div>

      {/* Arena sweep GPU slot indicator — shown when Tuning Lab is running */}
      <ArenaSlotIndicator arenaActive={data?.arena_active || 0} />

      {/* #336: manual scan tucked behind an "Advanced" disclosure — the Queue
          page is for monitoring; everyday queuing is Gaps + Library. */}
      <AdvancedScanDisclosure onSubmitted={() => refetch({ silent: false })} />

      {/* Processing — header padding 12x18 to align with the SUBMIT
          A MANUAL SCAN panel above. No maxHeight: auto-sizes to the
          number of concurrent transcribes (bounded by CONCURRENT_
          TRANSCRIPTIONS). Page scroll handles overflow if the user
          ever runs many parallel jobs. */}
      <div className="panel" style={{ padding: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden', flexShrink: 0 }}>
        <div style={{
          padding: '12px 18px',
          borderBottom: 'var(--border)',
          display: 'flex', alignItems: 'center', gap: 10,
        }}>
          <span className="label">Processing</span>
          <span className="num mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
            {processing.length}
          </span>
        </div>
        <div>
          <AsyncState
            loading={isInitialLoad}
            error={isError ? error : null}
            empty={!isInitialLoad && !isError && processing.length === 0}
            loadingMessage="Loading queue…"
            emptyMessage="Nothing transcribing right now."
            errorPrefix="Couldn't load queue">
            {processing.map((item, i) => (
              <QueueRow key={`p-${i}`} item={item} kind="processing" />
            ))}
          </AsyncState>
        </div>
      </div>

      {/* Queued — header padding aligned with Processing + submit panel.
          Auto-sizes; page scroll handles long queues. */}
      <div className="panel" style={{ padding: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden', flexShrink: 0 }}>
        <div style={{
          padding: '12px 18px',
          borderBottom: 'var(--border)',
          display: 'flex', alignItems: 'center', gap: 10,
        }}>
          <span className="label" title="Handed to subgen and waiting for the GPU. subarr feeds these from the Pending backlog below at your target depth — so this can be empty while Pending still has work.">Queued in subgen</span>
          <span className="num mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
            {queued.length}
          </span>
        </div>
        <div>
          <AsyncState
            empty={!isInitialLoad && !isError && queued.length === 0}
            emptyMessage="Nothing handed to subgen right now. New jobs wait in Pending below until subarr feeds them in.">
          {queued.map((item, i) => {
            const p = item.path || item.canonical_path || item.file || '';
            return (
              <QueueRow key={`q-${i}`} item={item} kind="queued"
                canCancel={canCancel}
                onCancel={cancelQueued}
                busy={busyAction === `cancel:${p}`} />
            );
          })}
          </AsyncState>
        </div>
      </div>

      {/* #66/#116: subarr's pending backlog (reorderable, pausable) — sits
          BELOW subgen's live Processing/Queued so the active trio reads
          Processing → Queued → Pending top-to-bottom. Self-hides when empty +
          not paused (no producers routed through it yet → usually empty for now). */}
      <PendingPanel />

      {/* #229: Lost on restart — subgen restarted before transcription
          completed. Surfaced separately from Issues because the failure
          mode is operational, not file-specific: requeue is always the
          right action. Hidden when empty. */}
      {orphaned.length > 0 && (
        <div className="panel" style={{ padding: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden', flexShrink: 0 }}>
          <div style={{
            padding: '12px 18px',
            borderBottom: 'var(--border)',
            display: 'flex', alignItems: 'center', gap: 10,
          }}>
            <QCheck checked={orphaned.length > 0 && orphaned.every(isSel)}
                    indeterminate={orphaned.some(isSel) && !orphaned.every(isSel)}
                    onChange={() => setSectionSel(orphaned, !orphaned.every(isSel))} />
            <span className="label" style={{ color: 'var(--warn-500)' }}>
              Lost on restart — subgen rebooted mid-flight
            </span>
            <span className="num mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
              {orphaned.length}
            </span>
            <span style={{ flex: 1 }} />
          </div>
          <div style={{ maxHeight: 320, overflowY: 'auto' }}>
            {orphaned.map((e) => (
              <HistoryRow key={`o-${e.scan_id}-${e.path}`} entry={e}
                          onRequeue={requeue} onRemove={removeOne}
                          checked={isSel(e)} onToggleSel={() => toggleSel(e)}
                          busy={busyAction === e.path || busyAction === e.scan_id} />
            ))}
          </div>
        </div>
      )}

      {/* v1.1.1 Featured Queue — Issues (skipped + failed) */}
      <div className="panel" style={{ padding: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden', flexShrink: 0 }}>
        <div style={{
          padding: '12px 18px',
          borderBottom: 'var(--border)',
          display: 'flex', alignItems: 'center', gap: 10,
        }}>
          {issues.length > 0 && (
            <QCheck checked={issues.every(isSel)}
                    indeterminate={issues.some(isSel) && !issues.every(isSel)}
                    onChange={() => setSectionSel(issues, !issues.every(isSel))} />
          )}
          <span className="label" style={{ color: issues.length ? 'var(--warn-500)' : undefined }}>
            Issues — silent fails subgen swallowed
          </span>
          <span className="num mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
            {issues.length}
          </span>
          <span style={{ flex: 1 }} />
          {issues.length > 0 && (
            <button className="btn ghost sm"
              onClick={() => clearByStatus(['error'], 'failed scans')}
              disabled={busyAction !== null}
              aria-label="Clear all failed scans from history">
              clear failed
            </button>
          )}
        </div>
        <div style={{ maxHeight: 320, overflowY: 'auto' }}>
          {issues.length === 0 ? (
            <div style={{ padding: 24, textAlign: 'center', color: 'var(--fg-3)', fontSize: 'var(--text-sm)' }}>
              No skipped or failed submissions in the last 24h.
            </div>
          ) : issues.map((e) => (
            <HistoryRow key={`i-${e.scan_id}-${e.path}`} entry={e}
                        onRequeue={requeue} onRemove={removeOne}
                        checked={isSel(e)} onToggleSel={() => toggleSel(e)}
                        busy={busyAction === e.path || busyAction === e.scan_id} />
          ))}
        </div>
      </div>

      {/* v1.1.1 Featured Queue — Recently done */}
      <div className="panel" style={{ padding: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden', flexShrink: 0 }}>
        <div style={{
          padding: '12px 18px',
          borderBottom: 'var(--border)',
          display: 'flex', alignItems: 'center', gap: 10,
        }}>
          {completedShown.length > 0 && (
            <QCheck checked={completedShown.every(isSel)}
                    indeterminate={completedShown.some(isSel) && !completedShown.every(isSel)}
                    onChange={() => setSectionSel(completedShown, !completedShown.every(isSel))} />
          )}
          <span className="label">Recently done</span>
          <span className="num mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
            {completed.length}
          </span>
          <span style={{ flex: 1 }} />
          {completed.length > 0 && (
            <button className="btn ghost sm"
              onClick={() => clearByStatus(['done'], 'completed scans')}
              disabled={busyAction !== null}
              aria-label="Clear all completed scans from history">
              clear done
            </button>
          )}
        </div>
        <div style={{ maxHeight: 320, overflowY: 'auto' }}>
          {completed.length === 0 ? (
            <div style={{ padding: 24, textAlign: 'center', color: 'var(--fg-3)', fontSize: 'var(--text-sm)' }}>
              No completed submissions in the last 24h.
            </div>
          ) : completedShown.map((e) => (
            <HistoryRow key={`d-${e.scan_id}-${e.path}`} entry={e}
                        onRequeue={requeue} onRemove={removeOne}
                        checked={isSel(e)} onToggleSel={() => toggleSel(e)}
                        busy={busyAction === e.path || busyAction === e.scan_id} />
          ))}
        </div>
      </div>

      {isEmpty && history.length === 0 && (
        <div style={{ padding: 32, textAlign: 'center', color: 'var(--fg-3)', fontSize: 'var(--text-sm)' }}>
          The queue is empty. Submit a path above or trigger a coverage re-walk to add jobs.
        </div>
      )}

      <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)', padding: '0 6px' }}>
        Featured Queue (v1.1.1): live subgen state + last 24h of submission outcomes from subarr's scan store. Nothing fails silently.
      </div>

      {/* Bulk action bar — appears when history rows are selected. */}
      {selRows.size > 0 && (
        <div style={{ position: 'sticky', bottom: 16, marginBottom: 16, zIndex: 5 }}>
          <div className="panel" style={{
            display: 'flex', alignItems: 'center', gap: 12,
            padding: '12px 16px',
            boxShadow: '0 6px 24px rgba(0,0,0,0.45)',
            border: '1px solid var(--violet-500)',
          }}>
            <span style={{ fontWeight: 600, color: 'var(--fg-0)' }}>{selRows.size} selected</span>
            <span style={{ flex: 1 }} />
            <button className="btn ghost sm" onClick={clearSel} disabled={bulkBusy !== null}>
              clear
            </button>
            <button className="btn sm" onClick={bulkRemove} disabled={bulkBusy !== null}
              title="Remove the selected entries from history (does not affect the live subgen queue)">
              {bulkBusy === 'remove' ? 'Removing…' : `✕ Remove (${selRows.size})`}
            </button>
            <button className="btn primary sm" onClick={bulkRequeue} disabled={bulkBusy !== null}
              title="Resubmit the selected paths to subgen as new scans">
              {bulkBusy === 'requeue' ? 'Requeuing…' : `↻ Requeue (${selRows.size})`}
            </button>
          </div>
        </div>
      )}
    </main>
  );
}
