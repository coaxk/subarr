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

import { StatusDot } from './atoms.jsx';

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
    <div style={{
      display: 'flex', flexDirection: 'column',
      padding: '10px 16px',
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
          <div style={{ flex: 1 }}>
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
  ok:      { fg: 'var(--success-500)', bg: 'rgba(52,211,153,0.10)',  br: 'rgba(52,211,153,0.35)', label: 'queued' },
  running: { fg: 'var(--violet-400)',  bg: 'rgba(139,92,246,0.10)',  br: 'rgba(139,92,246,0.35)', label: 'running' },
  skipped: { fg: 'var(--warn-500)',    bg: 'rgba(245,158,11,0.10)',  br: 'rgba(245,158,11,0.35)', label: 'skipped' },
  error:   { fg: 'var(--error-500)',   bg: 'rgba(239,68,68,0.10)',   br: 'rgba(239,68,68,0.35)',  label: 'failed' },
  pending: { fg: 'var(--fg-2)',        bg: 'var(--bg-2)',            br: 'var(--bg-4)',           label: 'pending' },
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

function HistoryRow({ entry, onRequeue, onRemove, busy }) {
  const path = entry.path;
  const out = entry.outcome || {};
  const category = out.category || 'pending';
  const ageS = Math.max(0, (Date.now() / 1000) - (entry.created_at || 0));
  const ageLabel = ageS < 60 ? `${Math.round(ageS)}s ago`
                  : ageS < 3600 ? `${Math.floor(ageS/60)}m ago`
                  : ageS < 86400 ? `${Math.floor(ageS/3600)}h ago`
                  : `${Math.floor(ageS/86400)}d ago`;
  // Requeue only makes sense for terminal outcomes (skipped/error/done).
  // For currently-running rows the action is hidden.
  const canRequeue = category === 'skipped' || category === 'error' || category === 'ok';
  return (
    <div style={{
      display: 'flex', flexDirection: 'column',
      padding: '10px 16px',
      borderBottom: '1px solid var(--bg-3)',
      gap: 4,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <OutcomeChip category={category} label={out.label} />
        <span className="mono" title={path} style={{
          flex: 1, fontSize: 'var(--text-xs)', color: 'var(--fg-1)',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          minWidth: 0,
        }}>{path}</span>
        <span className="mono" style={{
          fontSize: 'var(--text-2xs)', color: 'var(--fg-3)',
        }}>{ageLabel}</span>
        {canRequeue && (
          <button className="btn ghost sm"
            onClick={() => onRequeue && onRequeue(path)}
            disabled={busy}
            title="Resubmit this path to subgen as a new scan"
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

// ─── Page ────────────────────────────────────────────────────────
export function QueuePage() {
  const { data, loading, error, refetch } = useLiveQueue();
  const [busyAction, setBusyAction] = useState(null);  // scan_id or 'clear'

  const processing = data?.processing || [];
  const queued = data?.queued || [];
  const idle = data?.idle === true;
  const subgenVersion = data?.version;
  // v1.1.1: Featured Queue history. Split into Issues (skipped/error) vs
  // Recently done (ok/running) so the eye-catching problem rows are top.
  const history = data?.history || [];
  const issues = history.filter(h => {
    const c = h.outcome?.category;
    return c === 'skipped' || c === 'error';
  });
  const completed = history.filter(h => {
    const c = h.outcome?.category;
    return c === 'ok' || c === 'running';
  });
  const counts = data?.history_counts || {};
  // #58 v4.4: subgen advertises capabilities.queue_cancel via /queue.
  // When present, render the cancel button on queued rows; when absent
  // show the "needs v4.4+" hint so the user knows to upgrade.
  const canCancel = data?.capabilities?.queue_cancel === true;

  const requeue = useCallback(async (path) => {
    setBusyAction(path);
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
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22, lineHeight: 1.15, fontWeight: 600 }}>Queue</h1>
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
          <button className="btn" onClick={() => refetch()}>Refresh</button>
        </div>
      </div>

      {/* Submit manual scan */}
      <div className="panel" style={{ padding: '16px 18px' }}>
        <div className="label" style={{ marginBottom: 10 }}>Submit a manual scan</div>
        <SubmitScanForm onSubmitted={() => refetch({ silent: false })} />
      </div>

      {/* Processing — header sticky, body scrolls internally */}
      <div className="panel" style={{ padding: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{
          padding: '12px 16px',
          borderBottom: 'var(--border)',
          display: 'flex', alignItems: 'center', gap: 10,
        }}>
          <span className="label">Processing</span>
          <span className="num mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
            {processing.length}
          </span>
        </div>
        <div style={{ maxHeight: 280, overflowY: 'auto' }}>
          {isInitialLoad && (
            <div style={{ padding: 32, textAlign: 'center', color: 'var(--fg-2)' }}>Loading queue…</div>
          )}
          {isError && (
            <div style={{ padding: 32, textAlign: 'center', color: 'var(--error-500)' }}>
              Couldn't load queue: {String(error.message || error)}
            </div>
          )}
          {!isInitialLoad && !isError && processing.length === 0 && (
            <div style={{ padding: 24, textAlign: 'center', color: 'var(--fg-3)', fontSize: 'var(--text-sm)' }}>
              Nothing transcribing right now.
            </div>
          )}
          {processing.map((item, i) => (
            <QueueRow key={`p-${i}`} item={item} kind="processing" />
          ))}
        </div>
      </div>

      {/* Queued — header sticky, body scrolls internally */}
      <div className="panel" style={{ padding: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{
          padding: '12px 16px',
          borderBottom: 'var(--border)',
          display: 'flex', alignItems: 'center', gap: 10,
        }}>
          <span className="label">Queued</span>
          <span className="num mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
            {queued.length}
          </span>
        </div>
        <div style={{ maxHeight: 240, overflowY: 'auto' }}>
          {!isInitialLoad && !isError && queued.length === 0 && (
            <div style={{ padding: 24, textAlign: 'center', color: 'var(--fg-3)', fontSize: 'var(--text-sm)' }}>
              Nothing waiting in line.
            </div>
          )}
          {queued.map((item, i) => {
            const p = item.path || item.canonical_path || item.file || '';
            return (
              <QueueRow key={`q-${i}`} item={item} kind="queued"
                canCancel={canCancel}
                onCancel={cancelQueued}
                busy={busyAction === `cancel:${p}`} />
            );
          })}
        </div>
      </div>

      {/* v1.1.1 Featured Queue — Issues (skipped + failed) */}
      <div className="panel" style={{ padding: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{
          padding: '12px 16px',
          borderBottom: 'var(--border)',
          display: 'flex', alignItems: 'center', gap: 10,
        }}>
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
                        busy={busyAction === e.path || busyAction === e.scan_id} />
          ))}
        </div>
      </div>

      {/* v1.1.1 Featured Queue — Recently done */}
      <div className="panel" style={{ padding: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{
          padding: '12px 16px',
          borderBottom: 'var(--border)',
          display: 'flex', alignItems: 'center', gap: 10,
        }}>
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
          ) : completed.slice(0, 100).map((e) => (
            <HistoryRow key={`d-${e.scan_id}-${e.path}`} entry={e}
                        onRequeue={requeue} onRemove={removeOne}
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
    </main>
  );
}
