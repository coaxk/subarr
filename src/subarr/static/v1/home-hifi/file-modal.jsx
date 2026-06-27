// Activity page + per-file verdict modal — wired to /api/provenance/*.
//
// Activity surface polls GET /api/provenance/recent (10s, silent
// retries) and renders the ledger as a flat event log. Clicking a row
// fires GET /api/provenance/{canonical_path} and opens the modal with
// the ledger entries + the (optional) Bazarr history block for that
// file. Empty state is honest: "No activity yet — subarr writes a
// ledger entry when it queues, scans, or writes back a file."

import { StatusDot, LibraryChip } from './atoms.jsx';

const { useState, useEffect, useCallback, useMemo } = React;

const SOURCE_LABEL = {
  'subgenscan': 'subgen scan',
  'bazarr-via-subgen': 'bazarr · subgen',
  'bazarr-external': 'bazarr · external',
};

const KIND_DOT = {
  'subgenscan': 'violet',
  'bazarr-via-subgen': 'info',
  'bazarr-external': 'ok',
  'completed': 'ok',
  'queued': 'info',
  'unknown': 'muted',
};

// ─── Live data hooks ─────────────────────────────────────────────
export function useRecentProvenance(intervalMs = 10000) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const fetchOnce = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setLoading(true);
    try {
      const r = await fetch('/api/provenance/recent', { credentials: 'same-origin' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json()); setError(null);
    } catch (e) {
      setError((prev) => (data ? prev : e));
      // eslint-disable-next-line no-console
      console.debug('provenance/recent fetch failed:', e);
    } finally {
      if (!silent) setLoading(false);
    }
  }, [data]);
  useEffect(() => {
    let cancelled = false; let timer = null;
    async function tick() {
      if (cancelled) return;
      await fetchOnce({ silent: true });
      if (!cancelled) timer = setTimeout(tick, intervalMs);
    }
    (async () => { await fetchOnce(); if (!cancelled) timer = setTimeout(tick, intervalMs); })();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs]);
  return { data, loading, error, refetch: fetchOnce };
}

async function fetchProvenance(canonicalPath) {
  const r = await fetch(`/api/provenance/${encodeURI(canonicalPath)}`, { credentials: 'same-origin' });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// ─── Helpers ─────────────────────────────────────────────────────
function fmtClock(unix) {
  if (!unix) return '—';
  try { return new Date(unix * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }); }
  catch { return '—'; }
}

function fmtRelative(unix) {
  if (!unix) return '—';
  const now = Date.now() / 1000;
  const diff = Math.max(0, now - unix);
  if (diff < 60) return `${Math.round(diff)}s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  return `${Math.round(diff / 86400)}d ago`;
}

function classifyEntry(e) {
  // Derive a top-level "kind" the UI can render. The source field tells
  // us provider lineage; completed_at decides timeline stage.
  if (e.completed_at) return e.bazarr_scan_triggered_at ? 'written-back' : 'completed';
  if (e.queued_at) return 'queued';
  return 'unknown';
}

function shortName(canonical) {
  const parts = (canonical || '').split('/');
  return parts[parts.length - 1] || canonical || '—';
}

// ─── Activity row ────────────────────────────────────────────────
function ActivityRow({ a, focused, onClick }) {
  const kind = classifyEntry(a);
  const dotKey = a.completed_at ? (a.bazarr_scan_triggered_at ? 'bazarr-via-subgen' : 'completed') : 'queued';
  const meta = a.subgen_version ? `subgen v${a.subgen_version}` : SOURCE_LABEL[a.source] || a.source || '';
  const t = a.completed_at || a.queued_at;
  return (
    <div onClick={onClick} className="act-row" style={{
      display: 'grid',
      gridTemplateColumns: '14px 92px 110px 1fr auto',
      alignItems: 'center', gap: 12,
      padding: focused ? '0 16px 0 14px' : '0 16px',
      height: 34,
      borderBottom: '1px solid var(--bg-3)',
      background: focused ? 'rgba(139,92,246,0.06)' : 'transparent',
      borderLeft: focused ? '2px solid var(--violet-500)' : '2px solid transparent',
      cursor: 'pointer',
    }}>
      <StatusDot kind={KIND_DOT[dotKey] || 'muted'} />
      <span className="mono num" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>{fmtClock(t)}</span>
      <span style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-1)', fontWeight: focused ? 600 : 500 }}>{kind}</span>
      <span style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
        <span className="mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-1)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>{a.canonical_path}</span>
        <LibraryChip library={a.library} />
      </span>
      <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-2)' }}>{meta}</span>
    </div>
  );
}

const FILTERS = [
  { id: 'all', label: 'all', test: () => true },
  { id: 'completed', label: 'completed', test: (e) => !!e.completed_at },
  { id: 'queued', label: 'queued', test: (e) => !e.completed_at && !!e.queued_at },
  { id: 'written-back', label: 'written-back', test: (e) => !!e.bazarr_scan_triggered_at },
];

function ActivityFilters({ active, setActive, totalCount, filteredCount }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      {FILTERS.map((f) => (
        <span key={f.id} onClick={() => setActive(f.id)} style={{ cursor: 'pointer' }}
          className={`chip ${active === f.id ? 'violet' : ''}`}>
          {f.label}
        </span>
      ))}
      <span style={{ flex: 1 }} />
      <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }} className="num">
        {filteredCount} / {totalCount} ledger entries
      </span>
    </div>
  );
}

function exportNdjson(entries) {
  if (!entries?.length) return;
  const lines = entries.map((e) => JSON.stringify(e)).join('\n');
  const blob = new Blob([lines], { type: 'application/x-ndjson' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = `activity-${new Date().toISOString().slice(0, 10)}.ndjson`;
  a.click();
  URL.revokeObjectURL(url);
}

// ─── Live probe walks hook ───────────────────────────────────────
// Walks are a sibling stream to the provenance ledger — they update the
// embedded-sub cache, not the queued/scanned/written-back ledger entries
// Activity normally shows. We surface them at the top of Activity so
// "things are happening" is visible without users hunting through APIs.
function useLiveWalks(intervalMs = 3000) {
  const [data, setData] = useState(null);
  useEffect(() => {
    let cancelled = false; let timer = null;
    async function tick() {
      try {
        const r = await fetch('/api/probe/walks', { credentials: 'same-origin' });
        if (r.ok && !cancelled) setData(await r.json());
      } catch (e) {
        // eslint-disable-next-line no-console
        console.debug('walks fetch failed:', e);
      }
      if (!cancelled) timer = setTimeout(tick, intervalMs);
    }
    tick();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [intervalMs]);
  return data;
}

// ─── Walk progress banner ────────────────────────────────────────
// Lives at the top of Activity. Renders one row per non-finished walk,
// with progress bar + counts. Collapses entirely when no walks running.
function WalkProgressBanner() {
  const data = useLiveWalks();
  const walks = (data?.walks || []).filter((w) => w.status === 'running' || w.status === 'queued');
  if (walks.length === 0) return null;

  return (
    <div className="panel" style={{ padding: 'var(--row-cozy)', display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <StatusDot kind="info" pulse />
        <span className="label">probe walks · in flight</span>
        <span className="num mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>{walks.length}</span>
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>
          ffprobe results populate the embedded-EN cache · Coverage refreshes when done
        </span>
      </div>
      {walks.map((w) => {
        const pct = w.total_files > 0 ? Math.round((w.processed / w.total_files) * 100) : 0;
        const errors = (w.errors || []).length;
        return (
          <div key={w.id} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span className="mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)', width: 96 }}>{w.root}/</span>
            <div style={{ flex: 1, height: 6, background: 'var(--bg-3)', borderRadius: 3, overflow: 'hidden' }}>
              <div style={{ width: `${pct}%`, height: '100%', background: 'var(--violet-500)', transition: 'width var(--dur-base)' }} />
            </div>
            <span className="num mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-1)', width: 110, textAlign: 'right' }}>
              {w.processed.toLocaleString()} / {w.total_files.toLocaleString()}
            </span>
            <span className="num mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)', width: 50, textAlign: 'right' }}>{pct}%</span>
            <span className="num mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)', width: 100, textAlign: 'right' }}>
              <span style={{ color: 'var(--cyan-500)' }}>{w.cached_hits.toLocaleString()}</span> cache
              {errors > 0 && <span style={{ color: 'var(--error-500)' }}> · {errors} err</span>}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ─── Activity page body ──────────────────────────────────────────
function ActivityPageBody({ onOpenRow, focusedPath }) {
  const { data, loading, error, refetch } = useRecentProvenance();
  const [filter, setFilter] = useState('all');
  const [paused, setPaused] = useState(false);

  const entries = data?.entries || [];
  const filtered = useMemo(() => {
    const f = FILTERS.find((x) => x.id === filter) || FILTERS[0];
    return entries.filter(f.test);
  }, [entries, filter]);

  const isInitialLoad = loading && !data;
  const isError = error && !data;
  const isEmpty = !isInitialLoad && !isError && filtered.length === 0;

  return (
    <main className="main-canvas" style={{ padding: '22px 24px 22px', gap: 16, overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 'var(--text-h1)', lineHeight: 'var(--lh-h1)', fontWeight: 600, letterSpacing: '-0.005em' }}>Activity</h1>
          <div style={{ marginTop: 4, fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>
            Every probe, queue, scan, and write-back subarr has recorded. Click any row for the per-file verdict.
          </div>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <button className="btn" onClick={() => exportNdjson(filtered)} disabled={!filtered.length}>Export NDJSON</button>
          <button className="btn" onClick={() => setPaused((p) => !p)} title="Pause stops the 10s poll. Manual refresh button appears.">
            {paused ? 'Resume' : 'Pause'}
          </button>
          {paused && <button className="btn" onClick={() => refetch()}>Refresh</button>}
        </div>
      </div>

      <WalkProgressBanner />

      <ActivityFilters active={filter} setActive={setFilter} totalCount={entries.length} filteredCount={filtered.length} />

      <div className="panel" style={{ flex: 1, minHeight: 0, padding: 0, overflow: 'auto' }}>
        {isInitialLoad && (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--fg-2)' }}>Loading activity…</div>
        )}
        {isError && (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--error-500)' }}>
            Couldn't load activity: {String(error.message || error)}
            <div style={{ marginTop: 12 }}><button className="btn" onClick={() => refetch()}>Retry</button></div>
          </div>
        )}
        {isEmpty && (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--fg-2)' }}>
            {entries.length === 0
              ? 'No transcriptions yet. Activity records when subarr queues, scans, or writes back a subtitle file via subgen. Probe walks (which populate the embedded-EN cache) appear in the banner above when they’re running.'
              : `No entries match the "${filter}" filter.`}
          </div>
        )}
        {!isInitialLoad && !isError && filtered.map((a) => (
          <ActivityRow
            key={a.id}
            a={a}
            focused={a.canonical_path === focusedPath}
            onClick={() => onOpenRow && onOpenRow(a)}
          />
        ))}
      </div>
    </main>
  );
}

// ─── Modal: header ───────────────────────────────────────────────
function ModalHeader({ canonicalPath, suffix, ledger, onClose }) {
  const newest = (ledger || [])[0];
  return (
    <div style={{ padding: '16px 20px 14px', borderBottom: 'var(--border)', background: 'var(--bg-1)' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 4 }}>
            <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: 'var(--fg-0)' }}>
              {shortName(canonicalPath)}
            </h2>
            {suffix?.lang && (
              <span className="chip" style={{ height: 18, padding: '0 7px' }}>{suffix.lang}{suffix.provider ? ` · ${suffix.provider}` : ''}</span>
            )}
          </div>
          <div className="mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)', lineHeight: 1.5, wordBreak: 'break-all' }}>
            {canonicalPath || '—'}
          </div>
          {newest && (
            <div style={{ display: 'flex', gap: 14, marginTop: 8, fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
              {newest.subgen_version && <span><span style={{ color: 'var(--fg-3)' }}>subgen</span> <span className="mono">v{newest.subgen_version}</span></span>}
              {newest.source && <span><span style={{ color: 'var(--fg-3)' }}>source</span> <span className="mono">{SOURCE_LABEL[newest.source] || newest.source}</span></span>}
              {newest.completed_at && <span><span style={{ color: 'var(--fg-3)' }}>completed</span> <span className="mono">{fmtRelative(newest.completed_at)}</span></span>}
            </div>
          )}
        </div>
        <button onClick={onClose} aria-label="Close" style={{
          color: 'var(--fg-2)', fontSize: 18, lineHeight: 1,
          width: 28, height: 28,
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          borderRadius: 4, background: 'transparent', border: 'none', cursor: 'pointer',
        }}>×</button>
      </div>
    </div>
  );
}

// ─── Timeline ────────────────────────────────────────────────────
function buildTimeline(ledger, bazarrHistory) {
  const evs = [];
  for (const e of ledger || []) {
    if (e.queued_at) evs.push({
      kind: 'queued', t: e.queued_at, who: 'subarr',
      detail: `Queued ${e.scan_id ? `(scan ${e.scan_id})` : ''}`,
      chips: [['source', e.source || '—'], ['ep', e.sonarr_episode_id || '—']],
    });
    if (e.completed_at) evs.push({
      kind: 'scanned', t: e.completed_at, who: 'subgen',
      detail: `Subgen completed ${e.subgen_version ? `(v${e.subgen_version})` : ''}`,
      chips: [['source', e.source || '—']],
    });
    if (e.bazarr_scan_triggered_at) evs.push({
      kind: 'written-back', t: e.bazarr_scan_triggered_at, who: 'subarr → bazarr',
      detail: 'Bazarr scan-disk triggered after subgen wrote the file.',
    });
  }
  for (const h of bazarrHistory || []) {
    evs.push({
      kind: 'bazarr-history', t: h.timestamp_unix || h.timestamp || null, who: 'bazarr',
      detail: `${h.action || 'event'} · ${h.language || '?'} · provider ${h.provider || '?'}${h.score ? ` · score ${h.score}` : ''}`,
      raw: h,
    });
  }
  evs.sort((a, b) => (b.t || 0) - (a.t || 0));
  return evs;
}

const EVENT_COLOR = {
  'written-back': 'var(--success-500, var(--violet-400))',
  'scanned': 'var(--violet-500)',
  'queued': 'var(--cyan-500)',
  'bazarr-history': 'var(--fg-1)',
};

function TimelineEvent({ e, last }) {
  const color = EVENT_COLOR[e.kind] || 'var(--fg-2)';
  return (
    <div style={{ display: 'flex', gap: 14, position: 'relative' }}>
      <div style={{ position: 'relative', flex: '0 0 14px', display: 'flex', justifyContent: 'center', paddingTop: 4 }}>
        <span style={{ width: 10, height: 10, borderRadius: '50%', background: color, zIndex: 1 }} />
      </div>
      <div style={{ flex: 1, minWidth: 0, paddingBottom: last ? 0 : 20 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 4 }}>
          <span className="mono" style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--fg-0)' }}>{e.kind}</span>
          <span className="mono num" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>{fmtClock(e.t)}</span>
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>·</span>
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>{e.who}</span>
        </div>
        <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-1)', lineHeight: 1.5 }}>{e.detail}</div>
        {e.chips && (
          <div style={{ display: 'flex', gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
            {e.chips.map(([k, v]) => (
              <span key={k} className="mono" style={{
                fontSize: 'var(--text-2xs)', color: 'var(--fg-2)',
                background: 'var(--bg-2)', border: '1px solid var(--bg-4)',
                borderRadius: 2, padding: '1px 6px',
              }}>
                <span style={{ color: 'var(--fg-3)' }}>{k}</span> {v}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Modal ───────────────────────────────────────────────────────
function FileModal({ data, loading, error, canonicalPath, onClose, onShowRaw }) {
  const events = useMemo(() => data ? buildTimeline(data.ledger, data.bazarr?.history) : [], [data]);
  return (
    <div style={{
      position: 'fixed', inset: 0,
      background: 'rgba(10,10,12,0.62)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      padding: 40, zIndex: 50,
    }} onClick={onClose}>
      <div style={{
        width: 720, maxHeight: '88vh',
        background: 'var(--bg-1)', border: '1px solid var(--bg-4)',
        borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow-modal)',
        display: 'flex', flexDirection: 'column', overflow: 'hidden',
      }} onClick={(e) => e.stopPropagation()}>
        <ModalHeader
          canonicalPath={canonicalPath}
          suffix={data?.filename_suffix}
          ledger={data?.ledger}
          onClose={onClose} />
        <div style={{ flex: 1, minHeight: 0, padding: '18px 22px 18px 18px', overflow: 'auto', position: 'relative' }}>
          {loading && <div style={{ padding: 20, color: 'var(--fg-2)' }}>Loading verdict…</div>}
          {error && <div style={{ padding: 20, color: 'var(--error-500)' }}>Couldn't load verdict: {String(error.message || error)}</div>}
          {data && events.length === 0 && (
            <div style={{ padding: 20, color: 'var(--fg-2)' }}>
              No ledger entries for this file yet. {data.bazarr?.error ? `Bazarr error: ${data.bazarr.error}` : ''}
            </div>
          )}
          {data && events.length > 0 && (
            <>
              <div style={{ position: 'absolute', left: 18 + 7, top: 24, bottom: 24, width: 1, background: 'var(--bg-4)' }} />
              <div style={{ position: 'relative' }}>
                {events.map((e, i) => <TimelineEvent key={i} e={e} last={i === events.length - 1} />)}
              </div>
            </>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 20px', background: 'var(--bg-1)', borderTop: 'var(--border)' }}>
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>
            {data?.ledger?.length ? `${data.ledger.length} ledger entr${data.ledger.length === 1 ? 'y' : 'ies'}` : 'no ledger entries'}
            {data?.bazarr?.history?.length ? ` · ${data.bazarr.history.length} bazarr history rows` : ''}
          </span>
          <span style={{ flex: 1 }} />
          <button className="btn ghost" onClick={onShowRaw} disabled={!data}>View raw JSON</button>
        </div>
      </div>
    </div>
  );
}

function RawJsonModal({ data, onClose }) {
  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(10,10,12,0.8)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 40, zIndex: 60,
    }} onClick={onClose}>
      <div style={{
        width: 720, maxHeight: '88vh',
        background: 'var(--bg-1)', border: '1px solid var(--bg-4)',
        borderRadius: 'var(--radius-lg)', display: 'flex', flexDirection: 'column', overflow: 'hidden',
      }} onClick={(e) => e.stopPropagation()}>
        <div style={{ padding: '12px 20px', borderBottom: 'var(--border)', display: 'flex', alignItems: 'center' }}>
          <span className="label">raw response</span>
          <span style={{ flex: 1 }} />
          <button className="btn ghost" onClick={onClose}>Close</button>
        </div>
        <pre style={{
          margin: 0, padding: 16, overflow: 'auto', flex: 1,
          fontSize: 'var(--text-xs)', color: 'var(--fg-1)',
          background: 'var(--bg-0)', fontFamily: 'var(--font-mono)',
        }}>{JSON.stringify(data, null, 2)}</pre>
      </div>
    </div>
  );
}

// ─── Page wrapper ────────────────────────────────────────────────
export function FileModalPage() {
  const [openPath, setOpenPath] = useState(null);
  const [modalData, setModalData] = useState(null);
  const [modalLoading, setModalLoading] = useState(false);
  const [modalError, setModalError] = useState(null);
  const [showRaw, setShowRaw] = useState(false);

  const openRow = useCallback(async (entry) => {
    setOpenPath(entry.canonical_path);
    setModalData(null);
    setModalError(null);
    setModalLoading(true);
    try {
      const d = await fetchProvenance(entry.canonical_path);
      setModalData(d);
    } catch (e) {
      setModalError(e);
    } finally {
      setModalLoading(false);
    }
  }, []);

  const closeModal = useCallback(() => {
    setOpenPath(null); setModalData(null); setModalError(null); setShowRaw(false);
  }, []);

  return (
    <>
      <ActivityPageBody onOpenRow={openRow} focusedPath={openPath} />
      {openPath && (
        <FileModal
          data={modalData}
          loading={modalLoading}
          error={modalError}
          canonicalPath={openPath}
          onClose={closeModal}
          onShowRaw={() => setShowRaw(true)}
        />
      )}
      {showRaw && modalData && <RawJsonModal data={modalData} onClose={() => setShowRaw(false)} />}
    </>
  );
}
