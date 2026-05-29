// Library page — browse the media library, pick files/seasons/shows,
// send to queue.
//
// The killer feature: Coverage tells you what subarr THINKS you need
// next; Library lets you pick whatever YOU want. Multi-select across
// the whole tree, then submit as a single scan batch via /api/scan.
//
// Data: /api/browse?path=... returns one directory level at a time
// (lazy load on expand). Items have {name, path, is_dir, video_count,
// srt_count, has_sibling_srt, embedded_en, size_mb}.

import { StatusDot, Glyph } from './atoms.jsx';

const { useState, useEffect, useCallback, useMemo } = React;

// ─── Browse hook (per-path cache) ────────────────────────────────
// We cache the response for each visited path so re-expanding a node
// is instant; user-driven refresh forces a fresh fetch.
const browseCache = new Map();

async function fetchBrowse(path, { fresh = false } = {}) {
  const key = path || '';
  if (!fresh && browseCache.has(key)) return browseCache.get(key);
  // Skip recursive rollup at the root (TV/Movies have thousands of
  // files — rollup would take 10s+). User drills into a show to see
  // per-season rollup, which is fast (each season is small).
  const isRoot = !path;
  const qs = isRoot ? '?rollup=false' : `?path=${encodeURIComponent(path)}`;
  const r = await fetch('/api/browse' + qs, { credentials: 'same-origin' });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const d = await r.json();
  browseCache.set(key, d);
  return d;
}

// ─── Filter logic ────────────────────────────────────────────────
// Filters operate on the file_status / coverage_status from /api/browse.
// Dirs always pass so the tree structure stays navigable; the user
// filters within the tree by row state.
const FILTERS = [
  { id: 'all',     label: 'all',                  test: () => true },
  { id: 'missing', label: 'no English coverage',  test: (e) => e.is_dir || e.file_status === 'missing' },
  { id: 'covered', label: 'fully covered',        test: (e) => e.is_dir || e.file_status === 'covered' },
  { id: 'partial', label: 'partial coverage',     test: (e) => e.is_dir || e.file_status === 'srt-only' || e.file_status === 'embedded-only' },
  { id: 'unknown', label: 'not yet probed',       test: (e) => e.is_dir || e.file_status === 'unknown' },
];

// ─── Status indicators ───────────────────────────────────────────
// Folder rollup states drive the traffic light:
//   full     → every video has English coverage (disk srt or embedded)
//   partial  → some covered, some not (the main "needs work" state)
//   none     → no videos covered (and we've probed enough to be sure)
//   unknown  → empty dir, or < half probed → run a probe walk
//
// File states drive the per-row dot:
//   covered       → disk srt AND embedded
//   srt-only      → disk srt present, no English embedded
//   embedded-only → embedded EN/SDH present, no disk srt
//   missing       → no English from any source
//   unknown       → not yet probed
const STATUS_COLOR = {
  full: 'var(--success-500, #10b981)',
  partial: 'var(--warn-500, #f59e0b)',
  none: 'var(--error-500, #ef4444)',
  unknown: 'var(--fg-3)',
  covered: 'var(--success-500, #10b981)',
  'srt-only': 'var(--cyan-500, #06b6d4)',
  'embedded-only': 'var(--warn-500, #f59e0b)',
  missing: 'var(--error-500, #ef4444)',
};

function StatusDotIndicator({ status, size = 8 }) {
  const color = STATUS_COLOR[status] || 'var(--fg-3)';
  return (
    <span
      title={status || 'unknown'}
      style={{
        display: 'inline-block',
        width: size, height: size, borderRadius: '50%',
        background: color,
        flex: '0 0 auto',
      }} />
  );
}

function fmtDuration(secs) {
  if (!secs || !isFinite(secs)) return '';
  const m = Math.round(secs / 60);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h${m % 60}m`;
}

// ─── Tree node ───────────────────────────────────────────────────
function TreeNode({ entry, depth, selected, expanded, childrenData, childrenLoading, childrenError, onToggleSelect, onToggleExpand, search, filterFn }) {
  const isVisible = !search || entry.name.toLowerCase().includes(search.toLowerCase());
  const passesFilter = filterFn ? filterFn(entry) : true;
  if (!isVisible || !passesFilter) return null;

  const isSelected = selected.has(entry.path);
  const indent = depth * 18;
  const isVideo = !entry.is_dir;

  // Right-side meta: folders show rollup count, files show audio/sub langs.
  let metaText;
  if (entry.is_dir) {
    const probed = entry.videos_probed || 0;
    const total = entry.video_count || 0;
    const withEn = entry.videos_with_en || 0;
    metaText = total > 0
      ? `${withEn}/${total} EN${probed < total ? ` · ${probed} probed` : ''}`
      : '—';
  } else {
    const parts = [];
    if (entry.audio_langs?.length) parts.push(`audio: ${entry.audio_langs.join(',')}`);
    if (entry.sub_langs?.length) {
      const subList = entry.sub_langs.join(',');
      parts.push(`sub: ${subList}`);
    }
    if (entry.embedded_en) parts.push(`emb: ${entry.embedded_en}`);
    if (entry.duration_s) parts.push(fmtDuration(entry.duration_s));
    metaText = parts.join(' · ') || (entry.has_sibling_srt ? 'has .srt' : '—');
  }

  return (
    <>
      <div
        onClick={(e) => {
          e.stopPropagation();
          if (entry.is_dir) onToggleExpand(entry.path);
        }}
        style={{
          display: 'grid',
          gridTemplateColumns: `${indent}px 16px 12px 16px 1fr auto 70px`,
          alignItems: 'center', gap: 8,
          padding: '0 16px',
          height: 30,
          borderBottom: '1px solid var(--bg-3)',
          cursor: entry.is_dir ? 'pointer' : 'default',
          background: isSelected ? 'rgba(139,92,246,0.06)' : 'transparent',
          transition: 'background var(--dur-fast)',
        }}>
        <span />
        <span
          onClick={(e) => { e.stopPropagation(); onToggleSelect(entry.path, entry); }}
          style={{ display: 'inline-flex', cursor: 'pointer' }}>
          <CheckBox checked={isSelected} />
        </span>
        <StatusDotIndicator
          status={entry.is_dir ? entry.coverage_status : entry.file_status} />
        <span style={{ color: 'var(--fg-3)', fontSize: 'var(--text-xs)' }}>
          {entry.is_dir ? (expanded ? '▾' : '▸') : '·'}
        </span>
        <span style={{
          fontSize: 'var(--text-sm)',
          color: 'var(--fg-0)',
          fontFamily: isVideo ? 'var(--font-mono)' : 'inherit',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>{entry.name}{entry.is_dir ? '/' : ''}</span>
        <span className="mono" style={{
          fontSize: 'var(--text-2xs)',
          color: 'var(--fg-2)',
          textAlign: 'right',
          maxWidth: 480,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>{metaText}</span>
        <span className="num mono" style={{
          fontSize: 'var(--text-2xs)',
          color: 'var(--fg-3)',
          textAlign: 'right',
        }}>{entry.size_mb != null ? `${entry.size_mb.toFixed(0)} MB` : ''}</span>
      </div>
      {expanded && (
        <>
          {childrenLoading && (
            <div style={{ paddingLeft: indent + 32, color: 'var(--fg-3)', fontSize: 'var(--text-xs)', padding: '8px 16px' }}>
              Loading…
            </div>
          )}
          {childrenError && (
            <div style={{ paddingLeft: indent + 32, color: 'var(--error-500)', fontSize: 'var(--text-xs)', padding: '8px 16px' }}>
              Couldn't load: {String(childrenError.message || childrenError)}
            </div>
          )}
          {childrenData?.entries?.map((child) => (
            <ConnectedTreeNode
              key={child.path}
              entry={child}
              depth={depth + 1}
              selected={selected}
              onToggleSelect={onToggleSelect}
              search={search}
              filterFn={filterFn}
            />
          ))}
        </>
      )}
    </>
  );
}

function CheckBox({ checked }) {
  return (
    <span style={{
      display: 'inline-flex', width: 14, height: 14,
      border: `1px solid ${checked ? 'var(--violet-500)' : 'var(--bg-5)'}`,
      borderRadius: 3,
      background: checked ? 'var(--violet-500)' : 'transparent',
      alignItems: 'center', justifyContent: 'center',
      color: '#fff', fontSize: 10, lineHeight: 1,
    }}>{checked ? '✓' : ''}</span>
  );
}

// Connected wrapper: manages its own expand state + lazy child fetch.
// Hoisting this to a top-level component (rather than a recursive
// inline) keeps each node's local hooks isolated.
function ConnectedTreeNode({ entry, depth, selected, onToggleSelect, search, filterFn }) {
  const [expanded, setExpanded] = useState(false);
  const [childrenData, setChildrenData] = useState(null);
  const [childrenLoading, setChildrenLoading] = useState(false);
  const [childrenError, setChildrenError] = useState(null);

  const toggleExpand = useCallback(async (path) => {
    if (!entry.is_dir) return;
    const next = !expanded;
    setExpanded(next);
    if (next && !childrenData && !childrenLoading) {
      setChildrenLoading(true);
      setChildrenError(null);
      try {
        const d = await fetchBrowse(path);
        setChildrenData(d);
      } catch (e) {
        setChildrenError(e);
      } finally {
        setChildrenLoading(false);
      }
    }
  }, [entry.is_dir, expanded, childrenData, childrenLoading]);

  return (
    <TreeNode
      entry={entry}
      depth={depth}
      selected={selected}
      expanded={expanded}
      childrenData={childrenData}
      childrenLoading={childrenLoading}
      childrenError={childrenError}
      onToggleSelect={onToggleSelect}
      onToggleExpand={toggleExpand}
      search={search}
      filterFn={filterFn}
    />
  );
}

// ─── Selection bar ───────────────────────────────────────────────
function SelectionBar({ selectedPaths, onClear, onQueue, queueState }) {
  const n = selectedPaths.size;
  if (!n) return null;
  const busy = queueState?.busy;
  return (
    <div style={{
      position: 'sticky', bottom: 0,
      display: 'flex', alignItems: 'center', gap: 12,
      padding: '12px 18px',
      background: 'var(--bg-2)',
      border: 'var(--border)',
      borderRadius: 'var(--radius-lg)',
      boxShadow: '0 -2px 12px rgba(0,0,0,0.25)',
    }}>
      <CheckBox checked />
      <span style={{ fontSize: 'var(--text-md)', fontWeight: 600 }}>{n} selected</span>
      {queueState?.errors > 0 && (
        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--error-500)' }}>
          · {queueState.errors} failed
        </span>
      )}
      <span style={{ flex: 1 }} />
      <button className="btn ghost" onClick={onClear} disabled={busy}>Clear</button>
      <button className="btn primary" onClick={onQueue} disabled={busy}>
        {busy ? `Queueing ${queueState.done}/${queueState.total}…` : `Send ${n} to queue`}
      </button>
    </div>
  );
}

// ─── Page ────────────────────────────────────────────────────────
export function LibraryPage() {
  const [rootData, setRootData] = useState(null);
  const [rootLoading, setRootLoading] = useState(true);
  const [rootError, setRootError] = useState(null);

  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all');
  const [selected, setSelected] = useState(() => new Set());
  const [queueState, setQueueState] = useState({ busy: false, done: 0, total: 0, errors: 0 });
  const [queueResult, setQueueResult] = useState(null);

  const loadRoot = useCallback(async (opts = {}) => {
    setRootLoading(true); setRootError(null);
    try {
      if (opts.fresh) browseCache.clear();
      const d = await fetchBrowse('', { fresh: !!opts.fresh });
      setRootData(d);
    } catch (e) {
      setRootError(e);
    } finally {
      setRootLoading(false);
    }
  }, []);

  useEffect(() => { loadRoot(); }, [loadRoot]);

  const toggleSelect = useCallback((path) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path); else next.add(path);
      return next;
    });
  }, []);

  const clearSelection = useCallback(() => setSelected(new Set()), []);

  const filterFn = useMemo(() => {
    const f = FILTERS.find((x) => x.id === filter) || FILTERS[0];
    return f.test;
  }, [filter]);

  const sendToQueue = useCallback(async () => {
    const paths = Array.from(selected);
    if (!paths.length) return;
    setQueueState({ busy: true, done: 0, total: paths.length, errors: 0 });
    setQueueResult(null);
    // POST /api/scan accepts a list of paths in a single call — much
    // better than firing one request per path. We use the batch shape.
    try {
      const r = await fetch('/api/scan', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths, reverse: false }),
      });
      if (!r.ok && r.status !== 202) {
        const text = await r.text().catch(() => '');
        throw new Error(`HTTP ${r.status}: ${text.slice(0, 200)}`);
      }
      const body = await r.json().catch(() => ({}));
      setQueueState({ busy: false, done: paths.length, total: paths.length, errors: 0 });
      setQueueResult({ ok: true, scan_id: body.id, count: paths.length });
      setSelected(new Set());
    } catch (e) {
      setQueueState({ busy: false, done: 0, total: paths.length, errors: 1 });
      setQueueResult({ ok: false, error: e.message });
    }
  }, [selected]);

  const isInitialLoad = rootLoading && !rootData;
  const isError = rootError && !rootData;

  return (
    <main className="main-canvas" style={{ padding: '22px 24px 22px', gap: 14, overflow: 'auto' }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22, lineHeight: 1.15, fontWeight: 600 }}>Library</h1>
          <div style={{ marginTop: 4, fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>
            Browse your media library. Pick whatever you want — single files, full seasons, whole shows — and send them all to the queue.
          </div>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <button className="btn" onClick={() => loadRoot({ fresh: true })} disabled={rootLoading}>
            {rootLoading ? 'Refreshing…' : 'Refresh tree'}
          </button>
        </div>
      </div>

      {/* Search + filters */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          height: 30, padding: '0 12px',
          background: 'var(--bg-2)', border: 'var(--border)',
          borderRadius: 'var(--radius-md)', width: 280,
        }}>
          <Glyph char="⌕" size={12} color="var(--fg-3)" />
          <input
            type="text" value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="Search name…"
            style={{
              flex: 1, background: 'transparent', border: 'none',
              fontSize: 'var(--text-sm)', color: 'var(--fg-0)',
              outline: 'none',
            }} />
          {search && (
            <span onClick={() => setSearch('')} style={{ cursor: 'pointer', color: 'var(--fg-3)' }}>×</span>
          )}
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          {FILTERS.map((f) => (
            <span key={f.id} onClick={() => setFilter(f.id)} style={{ cursor: 'pointer' }}
              className={`chip ${filter === f.id ? 'violet' : ''}`}>
              {f.label}
            </span>
          ))}
        </div>
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>
          {selected.size > 0 ? `${selected.size} selected` : 'click a row to select'}
        </span>
      </div>

      {/* Queue result toast */}
      {queueResult && (
        <div style={{
          padding: '10px 14px',
          background: queueResult.ok ? 'rgba(52,211,153,0.06)' : 'rgba(239,68,68,0.06)',
          border: `1px solid ${queueResult.ok ? 'rgba(52,211,153,0.30)' : 'rgba(239,68,68,0.30)'}`,
          borderRadius: 'var(--radius-md)',
          display: 'flex', alignItems: 'center', gap: 10,
        }}>
          <StatusDot kind={queueResult.ok ? 'ok' : 'error'} />
          <span style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-0)' }}>
            {queueResult.ok
              ? `Submitted ${queueResult.count} path${queueResult.count === 1 ? '' : 's'} for scanning.${queueResult.scan_id ? ` Scan id: ${queueResult.scan_id}` : ''}`
              : `Submission failed: ${queueResult.error}`}
          </span>
          <span style={{ flex: 1 }} />
          {queueResult.ok && (
            <a href="/queue" className="btn sm" style={{ textDecoration: 'none' }}>View queue →</a>
          )}
          <span onClick={() => setQueueResult(null)} style={{ cursor: 'pointer', color: 'var(--fg-3)' }}>×</span>
        </div>
      )}

      {/* Tree */}
      <div className="panel" style={{
        flex: 1, minHeight: 0,
        padding: 0,
        display: 'flex', flexDirection: 'column',
        overflow: 'hidden',
      }}>
        <div style={{
          display: 'grid',
          gridTemplateColumns: '16px 12px 16px 1fr auto 70px',
          alignItems: 'center', gap: 8,
          padding: '0 16px', height: 32,
          background: 'var(--bg-1)',
          borderBottom: 'var(--border)',
          position: 'sticky', top: 0, zIndex: 2,
        }}>
          <span />
          <span title="Coverage status">●</span>
          <span />
          <span style={{ fontSize: 'var(--text-2xs)', textTransform: 'uppercase', letterSpacing: '0.10em', color: 'var(--fg-2)' }}>name</span>
          <span style={{ fontSize: 'var(--text-2xs)', textTransform: 'uppercase', letterSpacing: '0.10em', color: 'var(--fg-2)', textAlign: 'right' }}>audio / sub / runtime</span>
          <span style={{ fontSize: 'var(--text-2xs)', textTransform: 'uppercase', letterSpacing: '0.10em', color: 'var(--fg-2)', textAlign: 'right' }}>size</span>
        </div>
        <div style={{ flex: 1, overflow: 'auto' }}>
          {isInitialLoad && (
            <div style={{ padding: 40, textAlign: 'center', color: 'var(--fg-2)' }}>Loading library…</div>
          )}
          {isError && (
            <div style={{ padding: 40, textAlign: 'center', color: 'var(--error-500)' }}>
              Couldn't load library: {String(rootError.message || rootError)}
              <div style={{ marginTop: 12 }}>
                <button className="btn" onClick={() => loadRoot()}>Retry</button>
              </div>
            </div>
          )}
          {rootData && rootData.entries?.length === 0 && (
            <div style={{ padding: 40, textAlign: 'center', color: 'var(--fg-2)' }}>
              Library is empty. Check that subarr's library mount points to your media.
            </div>
          )}
          {rootData?.entries?.map((e) => (
            <ConnectedTreeNode
              key={e.path}
              entry={e}
              depth={0}
              selected={selected}
              onToggleSelect={toggleSelect}
              search={search}
              filterFn={filterFn}
            />
          ))}
        </div>
      </div>

      {/* Selection bar */}
      <div style={{ position: 'sticky', bottom: 16 }}>
        <SelectionBar
          selectedPaths={selected}
          onClear={clearSelection}
          onQueue={sendToQueue}
          queueState={queueState}
        />
      </div>
    </main>
  );
}
