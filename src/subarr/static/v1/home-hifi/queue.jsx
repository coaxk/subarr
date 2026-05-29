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

function ProgressBar({ pct }) {
  const value = Math.max(0, Math.min(100, pct || 0));
  return (
    <div style={{
      width: 120, height: 4,
      background: 'var(--bg-3)',
      borderRadius: 2, overflow: 'hidden',
    }}>
      <div style={{
        width: `${value}%`, height: '100%',
        background: 'var(--violet-500)',
        transition: 'width var(--dur-base) var(--ease-out)',
      }} />
    </div>
  );
}

function QueueRow({ item, kind }) {
  // Different upstream shapes — subgen's processing entries have:
  // {path, percentage_complete?, status?, started_at?, ...}
  // Queued entries are usually just {path, position?}
  const path = item.path || item.canonical_path || item.file || JSON.stringify(item).slice(0, 80);
  const pct = item.percentage_complete ?? item.percent ?? item.progress ?? null;
  const stage = item.stage || item.status || (kind === 'processing' ? 'transcribing' : 'queued');
  const elapsed = item.started_at ? (Date.now()/1000 - item.started_at) : null;
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '16px 1fr 140px 70px 80px',
      alignItems: 'center', gap: 12,
      padding: '0 16px',
      height: 38,
      borderBottom: '1px solid var(--bg-3)',
    }}>
      <StatusDot kind={kind === 'processing' ? 'violet' : 'info'} pulse={kind === 'processing'} />
      <span className="mono" style={{
        fontSize: 'var(--text-xs)',
        color: 'var(--fg-1)',
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>{path}</span>
      <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-2)', letterSpacing: '0.04em', textTransform: 'uppercase' }}>{stage}</span>
      <ProgressBar pct={pct} />
      <span className="num mono" style={{
        fontSize: 'var(--text-xs)',
        color: 'var(--fg-2)',
        textAlign: 'right',
      }}>{pct != null ? fmtPct(pct) : elapsed ? fmtDuration(elapsed) : '—'}</span>
    </div>
  );
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

  const processing = data?.processing || [];
  const queued = data?.queued || [];
  const idle = data?.idle === true;
  const subgenVersion = data?.version;

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

      {/* Processing */}
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

      {/* Queued */}
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
        {!isInitialLoad && !isError && queued.length === 0 && (
          <div style={{ padding: 24, textAlign: 'center', color: 'var(--fg-3)', fontSize: 'var(--text-sm)' }}>
            Nothing waiting in line.
          </div>
        )}
        {queued.map((item, i) => (
          <QueueRow key={`q-${i}`} item={item} kind="queued" />
        ))}
      </div>

      {isEmpty && (
        <div style={{ padding: 32, textAlign: 'center', color: 'var(--fg-3)', fontSize: 'var(--text-sm)' }}>
          The queue is empty. Submit a path above or trigger a coverage re-walk to add jobs.
        </div>
      )}

      <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)', padding: '0 6px' }}>
        v1.0 ships read-only. Start / stop / remove / reorder controls land in v1.0.x once the corresponding subgen endpoints are wired.
      </div>
    </main>
  );
}
