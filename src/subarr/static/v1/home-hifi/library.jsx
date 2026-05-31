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

const { useState, useEffect, useCallback, useMemo, useRef } = React;

// ─── Alphabet jump rail ──────────────────────────────────────────
// Narrow right-side A-Z column that adapts to whatever's currently
// expanded in the tree. Scans the live DOM for [data-lib-letter] which
// TreeNode stamps on every directory row at every depth — so when the
// user drills into TV/ the rail re-populates with show letters, and
// when they collapse back the rail re-scopes to top-level letters.
//
// MutationObserver watches the scroll container so async child loads
// (browse fetches when a folder expands) flow into the rail without us
// manually re-firing anything.
function AlphabetRail({ containerRef }) {
  const [available, setAvailable] = useState(() => new Set());

  // Recompute available letters from the current DOM.
  const recompute = useCallback(() => {
    const root = containerRef?.current;
    if (!root) { setAvailable(new Set()); return; }
    const nodes = root.querySelectorAll('[data-lib-letter]');
    const s = new Set();
    nodes.forEach((n) => {
      const L = n.getAttribute('data-lib-letter');
      if (L) s.add(L);
    });
    setAvailable(s);
  }, [containerRef]);

  useEffect(() => {
    const root = containerRef?.current;
    if (!root) return;
    recompute();
    // Watch the tree subtree for any node addition/removal so async
    // child loads + collapse/expand updates flow straight in.
    const obs = new MutationObserver(() => recompute());
    obs.observe(root, { childList: true, subtree: true });
    return () => obs.disconnect();
  }, [containerRef, recompute]);

  const letters = ['#', ..."ABCDEFGHIJKLMNOPQRSTUVWXYZ".split('')];
  const jump = useCallback((letter) => {
    const root = containerRef?.current;
    if (!root) return;
    // Pick the FIRST matching node in document order — naturally scopes
    // to the topmost match at whatever depth is expanded. If user has
    // both TV and Movies open and clicks K, they land on the first K
    // either contains.
    const target = root.querySelector(`[data-lib-letter="${letter}"]`);
    if (!target) return;
    // scrollIntoView would scroll the *page*; we want the tree container
    // to scroll. Compute offset relative to root + set scrollTop directly.
    const rootRect = root.getBoundingClientRect();
    const tgtRect = target.getBoundingClientRect();
    root.scrollTop += tgtRect.top - rootRect.top;
  }, [containerRef]);
  return (
    <div role="navigation" aria-label="Jump to letter" style={{
      flex: '0 0 22px',
      display: 'flex', flexDirection: 'column',
      alignItems: 'stretch', justifyContent: 'space-between',
      padding: '2px 0',
      fontFamily: 'var(--font-mono)',
      fontSize: 'var(--text-2xs)',
      userSelect: 'none',
    }}>
      {letters.map((L) => {
        const has = available.has(L);
        return (
          <button key={L}
            onClick={() => has && jump(L)}
            disabled={!has}
            aria-label={has ? `Jump to ${L}` : `No entries starting with ${L}`}
            title={has ? `Jump to ${L}` : `No entries starting with ${L}`}
            style={{
              flex: 1,
              padding: 0,
              background: 'transparent', border: 'none',
              color: has ? 'var(--fg-2)' : 'var(--bg-5)',
              cursor: has ? 'pointer' : 'default',
              transition: 'color var(--dur-fast)',
              lineHeight: 1,
            }}
            onMouseEnter={(e) => { if (has) e.currentTarget.style.color = 'var(--violet-400)'; }}
            onMouseLeave={(e) => { if (has) e.currentTarget.style.color = 'var(--fg-2)'; }}>
            {L}
          </button>
        );
      })}
    </div>
  );
}

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

const STATUS_TIPS = {
  'covered':       'COVERED — disk .srt sidecar present AND embedded English track present',
  'srt-only':      'SRT ONLY — .srt sidecar on disk, no embedded English track',
  'embedded-only': 'EMBEDDED ONLY — English subtitle track embedded in file, no separate .srt sidecar',
  'missing':       'MISSING — no English from any source (no .srt sidecar, no embedded English track)',
  'unknown':       'NOT PROBED — subarr hasn\'t ffprobed this file yet. Run a probe walk.',
};
function StatusDotIndicator({ status, size = 8 }) {
  const color = STATUS_COLOR[status] || 'var(--fg-3)';
  const tip = STATUS_TIPS[status] || `status: ${status || 'unknown'}`;
  return (
    <span
      title={tip}
      style={{
        display: 'inline-block',
        width: size, height: size, borderRadius: '50%',
        background: color,
        flex: '0 0 auto',
        cursor: 'help',
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
// #211: when an ancestor directory is selected, every descendant counts as
// implicitly selected too (the scan runner walks recursively from the
// ancestor path). Compute "inherited" so child checkboxes render checked
// instead of looking unchecked underneath a ticked parent.
//
// Selection set holds canonical paths (POSIX-style "TV/Cheers/Season 1");
// a path P inherits selection if some entry S in `selected` is a strict
// prefix of P at a path-segment boundary. Cheap O(|selected|) check per
// node; selected sets are tiny relative to the tree.
function isUnderSelectedAncestor(path, selected) {
  if (!selected || selected.size === 0) return false;
  for (const s of selected) {
    if (s === path) continue;
    if (path.startsWith(s + '/')) return true;
  }
  return false;
}

function TreeNode({ entry, depth, selected, expanded, childrenData, childrenLoading, childrenError, onToggleSelect, onToggleExpand, search, filterFn }) {
  // #194: search visibility. The tree is lazily loaded, so we can't
  // "find" matches inside un-expanded subtrees synchronously. Strategy:
  //   - depth 0 (TV/Movies category roots): always visible during search
  //     so users see the path to their match.
  //   - depth >= 1 directories + leaf files: name-substring match only.
  // Result: typing "cheers" shows TV/ → Cheers/ → episodes, instead of
  // hiding the root TV/ folder because its name doesn't contain "cheers".
  const searchActive = !!(search && search.trim());
  const nameMatches = !search || entry.name.toLowerCase().includes(search.toLowerCase());
  const isVisible = searchActive && entry.is_dir && depth === 0
    ? true                  // category roots stay visible while searching
    : nameMatches;          // everything else: must match
  const passesFilter = filterFn ? filterFn(entry) : true;
  if (!isVisible || !passesFilter) return null;

  const isSelected = selected.has(entry.path);
  // #211: visually mark descendants of a selected directory as checked
  // (they ARE in the implicit scan set; the parent path covers them).
  const isInherited = !isSelected && isUnderSelectedAncestor(entry.path, selected);
  const isVisuallyChecked = isSelected || isInherited;
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

  // Alphabet jump: every directory entry, at any depth, is a jump target.
  // The rail uses live DOM queries against [data-lib-letter] so it adapts
  // as the user drills deeper into the tree.
  const _firstChar = (entry.name || '').trimStart().charAt(0).toUpperCase();
  const _jumpLetter = entry.is_dir
    ? (/[A-Z]/.test(_firstChar) ? _firstChar : '#')
    : undefined;  // skip files — too noisy and identifies nothing useful
  return (
    <>
      <div
        data-lib-letter={_jumpLetter}
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
          background: isVisuallyChecked ? 'rgba(139,92,246,0.06)' : 'transparent',
          transition: 'background var(--dur-fast)',
        }}>
        <span />
        <span
          onClick={(e) => {
            e.stopPropagation();
            // #211: clicks on an inherited-checked node are no-ops —
            // unticking it wouldn't actually exclude this node from the
            // scan (the ancestor's path still covers it). Untick the
            // ancestor first if you want finer-grain selection.
            if (isInherited) return;
            onToggleSelect(entry.path, entry);
          }}
          title={isInherited ? 'Covered by an ancestor selection — untick the parent to deselect' : undefined}
          style={{
            display: 'inline-flex',
            cursor: isInherited ? 'not-allowed' : 'pointer',
            opacity: isInherited ? 0.6 : 1,
          }}>
          <CheckBox checked={isVisuallyChecked} />
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
  const [userExpanded, setUserExpanded] = useState(false);
  const [childrenData, setChildrenData] = useState(null);
  const [childrenLoading, setChildrenLoading] = useState(false);
  const [childrenError, setChildrenError] = useState(null);

  // #194: when search is active, force-expand the category-root directories
  // (depth 0: TV, Movies) so users can SEE their search matches at depth 1.
  // Without this, the root TV/ folder stays collapsed and the user thinks
  // search is broken because their series doesn't appear.
  //
  // Past depth 0 we only expand explicit user choices — auto-expanding all
  // matching series would otherwise fetch every season+episode for every
  // series whose name contains the search term, which on a 1700-show
  // library can be hundreds of HTTP calls per keystroke.
  const searchActive = !!(search && search.trim());
  const expanded = (searchActive && entry.is_dir && depth === 0) || userExpanded;

  const fetchChildren = useCallback(async (path) => {
    if (!entry.is_dir || childrenData || childrenLoading) return;
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
  }, [entry.is_dir, childrenData, childrenLoading]);

  // Auto-fetch children when search forces us open. Debounced via the
  // browseCache inside fetchBrowse, so re-typing reuses cached payloads.
  useEffect(() => {
    if (expanded) fetchChildren(entry.path);
  }, [expanded, entry.path, fetchChildren]);

  const toggleExpand = useCallback((path) => {
    if (!entry.is_dir) return;
    // While search is active, user clicks just toggle the user's own
    // expansion preference for after-the-search-clears. The effective
    // expanded state stays forced-open until search empties.
    setUserExpanded((prev) => !prev);
    if (!userExpanded) fetchChildren(path);
  }, [entry.is_dir, userExpanded, fetchChildren]);

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
  // [2026-05-30] Switched to position:fixed at viewport bottom so the
  // bar is always visible regardless of where the user is in the tree.
  // The Coverage page uses the same pattern. position:sticky required
  // scrolling all the way to the bottom of the column to see it, which
  // defeats the purpose on a 1700-show library.
  return (
    <div style={{
      position: 'fixed', bottom: 18, left: 200, right: 24,
      display: 'flex', alignItems: 'center', gap: 12,
      padding: '12px 18px',
      background: 'var(--bg-2)',
      border: 'var(--border)',
      borderRadius: 'var(--radius-lg)',
      boxShadow: '0 -2px 18px rgba(0,0,0,0.45)',
      zIndex: 10,
    }}>
      <CheckBox checked />
      {/* #211 follow-up: the count is the number of EXPLICIT picks the user
          ticked. Each pick that's a directory expands recursively at scan
          time (the runner walks every file under it). "1 pick" reads
          honest when a single folder covers many files; "1 selected"
          implied the user had ticked only one row visible. */}
      <span style={{ fontSize: 'var(--text-md)', fontWeight: 600 }}>
        {n} {n === 1 ? 'pick' : 'picks'}
      </span>
      <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>
        · folders include everything inside
      </span>
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
  // Ref for the inner tree-scroll container so the AlphabetRail can drive it.
  const treeScrollRef = useRef(null);

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
    <main className="main-canvas" style={{ padding: '22px 24px 22px', gap: 14, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
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
          {selected.size > 0
            ? `${selected.size} ${selected.size === 1 ? 'pick' : 'picks'}`
            : 'click a row to select'}
        </span>
      </div>

      {/* Legend for the status dots — users couldn't tell what the colors meant */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 14,
        padding: '6px 10px',
        fontSize: 'var(--text-2xs)',
        color: 'var(--fg-3)',
        background: 'var(--bg-1)',
        border: 'var(--border)',
        borderRadius: 'var(--radius-md)',
        flexWrap: 'wrap',
      }}>
        <span style={{ color: 'var(--fg-2)', letterSpacing: '0.04em', textTransform: 'uppercase' }}>status</span>
        {[
          { c: 'var(--success-500, #10b981)', label: 'covered / full' },
          { c: 'var(--cyan-500, #06b6d4)', label: 'srt only' },
          { c: 'var(--warn-500, #f59e0b)', label: 'embedded only / partial' },
          { c: 'var(--error-500, #ef4444)', label: 'missing / none' },
          { c: 'var(--fg-3)', label: 'unknown — run probe' },
        ].map((x) => (
          <span key={x.label} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: x.c, display: 'inline-block' }} />
            <span>{x.label}</span>
          </span>
        ))}
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

      {/* Tree + alphabet jump rail. Tree on the left, narrow A-Z column
          on the right. Letters are clickable when there's at least one
          top-level entry starting with that letter; greyed otherwise.
          Hidden when search is active — search results are scoped enough
          that an alphabet jump doesn't help. */}
      <div style={{ flex: 1, minHeight: 0, display: 'flex', gap: 8 }}>
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
          <div ref={treeScrollRef} style={{ flex: 1, overflow: 'auto' }}>
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
              // data-lib-letter lives on every depth's row inside TreeNode,
              // so the alphabet rail's querySelectorAll naturally finds
              // jump targets at whatever depth is currently expanded.
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
        {!search.trim() && rootData?.entries?.length > 0 && (
          <AlphabetRail containerRef={treeScrollRef} />
        )}
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
