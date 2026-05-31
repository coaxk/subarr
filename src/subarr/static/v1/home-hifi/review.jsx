// v1.1.1: dedicated audio-language review queue page.
//
// Tree-grouped by series with bulk assignment: user picks a target
// language, ticks one or more series (or individual episodes), and
// applies the verification across the selection in one click. Each
// individual verify still runs through the full pipeline — local
// audio_lang_store + Sonarr propagation + Bazarr sync trigger — so
// bulk-assigning Spanish to a whole telenovela closes the bazarr-blind
// loop for every episode at once.

import { StatusDot } from './atoms.jsx';
import { AudioReviewModal } from './coverage.jsx';

const { useState, useEffect, useCallback, useMemo } = React;

// Language pick list — same set Coverage's per-row modal uses, so the
// vocabulary is identical between flows. Alphabetical by English name.
// Expanded 2026-05-31 with Balkan + Baltic + slavic langs Judd flagged
// as missing (Serbian / Bulgarian / Croatian).
const LANG_PICKS = [
  ['ara','Arabic'],
  ['bul','Bulgarian'],
  ['cat','Catalan'],
  ['chi','Chinese'],
  ['hrv','Croatian'],
  ['cze','Czech'],
  ['dan','Danish'],
  ['dut','Dutch'],
  ['eng','English'],
  ['est','Estonian'],
  ['fin','Finnish'],
  ['fre','French'],
  ['ger','German'],
  ['gre','Greek'],
  ['heb','Hebrew'],
  ['hin','Hindi'],
  ['hun','Hungarian'],
  ['ind','Indonesian'],
  ['ita','Italian'],
  ['jpn','Japanese'],
  ['kor','Korean'],
  ['lav','Latvian'],
  ['lit','Lithuanian'],
  ['may','Malay'],
  ['nor','Norwegian'],
  ['pol','Polish'],
  ['por','Portuguese'],
  ['rum','Romanian'],
  ['rus','Russian'],
  ['srp','Serbian'],
  ['slo','Slovak'],
  ['slv','Slovenian'],
  ['spa','Spanish'],
  ['swe','Swedish'],
  ['tha','Thai'],
  ['tur','Turkish'],
  ['ukr','Ukrainian'],
  ['vie','Vietnamese'],
];

function FlagDot({ flag }) {
  const kind = flag === 'suspect' ? 'warn' : 'muted';
  const tip = flag === 'suspect'
    ? "File metadata likely lies — claims English on a foreign show"
    : "ffprobe couldn't determine audio language";
  return <span title={tip}><StatusDot kind={kind} /></span>;
}

function CheckBox({ checked, indeterminate, onChange, label }) {
  const ref = React.useRef(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = !!indeterminate && !checked;
  }, [indeterminate, checked]);
  return (
    <input
      ref={ref}
      type="checkbox"
      checked={checked}
      onChange={onChange}
      aria-label={label}
      onClick={(e) => e.stopPropagation()}
      style={{
        cursor: 'pointer',
        width: 14, height: 14,
        accentColor: 'var(--violet-500)',
      }} />
  );
}

function EpisodeRow({ item, selected, onToggle, onOpen }) {
  const audio = (item.audio_langs || []).join(',') || 'und';
  const detail = {
    title: item.title,
    ep: item.episode_number || '',
    _canonical_path: item.file_canonical_path || item.canonical_path,
    audio,
    original_language: item.original_language,
    audio_label_notes: item.notes ? [item.notes] : [],
  };
  const path = item.file_canonical_path || item.canonical_path;
  return (
    <div role="row" style={{
      display: 'grid',
      gridTemplateColumns: '40px 14px 80px 1fr 70px 70px 22px',
      alignItems: 'center', gap: 10,
      padding: '8px 16px 8px 32px',
      borderBottom: '1px solid var(--bg-3)',
      background: selected ? 'rgba(139,92,246,0.04)' : 'transparent',
      transition: 'background 90ms ease',
    }}>
      <CheckBox checked={selected}
                onChange={onToggle}
                label={`Select ${item.title} ${item.episode_number}`} />
      <FlagDot flag={item.flag} />
      <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-2)' }}>
        {item.episode_number || '—'}
      </span>
      <span className="mono" title={path} style={{
        fontSize: 'var(--text-2xs)', color: 'var(--fg-3)',
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0,
      }}>
        {path.split('/').slice(-1)[0]}
      </span>
      <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-2)' }}>
        audio: {audio}
      </span>
      <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-2)' }}>
        {item.original_language || '—'}
      </span>
      <button onClick={() => onOpen(detail)}
        aria-label={`Listen to ${item.title} ${item.episode_number}`}
        title="Listen + verify individually"
        className="btn ghost sm"
        style={{ padding: '0 6px', fontSize: 'var(--text-2xs)' }}>🎧</button>
    </div>
  );
}

function SeriesGroup({ series, expanded, onToggleExpand, selected, onToggleSelectAll,
                      epSelection, onToggleEp, onOpenEp }) {
  const eps = series.items;
  const epIds = eps.map((e) => e.file_canonical_path || e.canonical_path);
  const checkedCount = epIds.filter((id) => epSelection.has(id)).length;
  const allChecked = checkedCount === epIds.length;
  const indeterminate = checkedCount > 0 && !allChecked;
  return (
    <>
      <div role="row"
        onClick={onToggleExpand}
        style={{
          display: 'grid',
          gridTemplateColumns: '14px 14px 1fr 80px 90px',
          alignItems: 'center', gap: 10,
          padding: 'var(--row-cozy)',
          borderBottom: '1px solid var(--bg-3)',
          background: 'var(--bg-1)',
          cursor: 'pointer',
        }}>
        <CheckBox checked={allChecked}
                  indeterminate={indeterminate}
                  onChange={() => onToggleSelectAll(epIds, !allChecked)}
                  label={`Select all ${eps.length} episodes of ${series.title}`} />
        <span style={{ color: 'var(--fg-3)', fontSize: 'var(--text-xs)' }}>
          {expanded ? '▾' : '▸'}
        </span>
        <span style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-0)', fontWeight: 500 }}>
          {series.title}
          {series.original_language && (
            <span className="mono" style={{ color: 'var(--fg-3)', fontWeight: 400, marginLeft: 10, fontSize: 'var(--text-2xs)' }}>
              orig: {series.original_language}
            </span>
          )}
        </span>
        <span className="mono num" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-2)' }}>
          {eps.length} {eps.length === 1 ? 'ep' : 'eps'}
        </span>
        <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>
          {checkedCount > 0 ? `${checkedCount} selected` : ''}
        </span>
      </div>
      {expanded && eps.map((it) => {
        const id = it.file_canonical_path || it.canonical_path;
        return (
          <EpisodeRow key={id}
                      item={it}
                      selected={epSelection.has(id)}
                      onToggle={() => onToggleEp(id)}
                      onOpen={onOpenEp} />
        );
      })}
    </>
  );
}

export function ReviewPage() {
  const [data, setData] = useState(null);
  // loading = first paint only (data === null). After we've ever rendered
  // a list, refetches go through isRefetching so the stale list stays on
  // screen and the user just sees a small inline spinner — #225.
  const [loading, setLoading] = useState(true);
  const [isRefetching, setIsRefetching] = useState(false);
  const [error, setError] = useState(null);
  // User feedback (2026-05-31): "Refresh" button looked broken because the
  // pending-review fetch returns in <100ms — the spinner flashed below the
  // human perception floor. Track the last refresh time so we can render
  // an "updated Xs ago" stamp that visibly resets on every click. Combined
  // with a minimum-display-time on the inline spinner (in fetchPending),
  // the user can SEE that something happened.
  const [lastRefreshedAt, setLastRefreshedAt] = useState(0);
  // Tick at 5s intervals so the "updated Xs ago" label stays current.
  const [nowTick, setNowTick] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNowTick(Date.now()), 5000);
    return () => clearInterval(id);
  }, []);
  const [filter, setFilter] = useState('all');
  const [search, setSearch] = useState('');
  // Selection: file_canonical_path (or canonical_path) for each ticked episode.
  const [epSelection, setEpSelection] = useState(() => new Set());
  // Series-level expansion state.
  const [expandedSeries, setExpandedSeries] = useState(() => new Set());
  // Bulk apply state.
  const [bulkLang, setBulkLang] = useState('fre');
  const [bulkRunning, setBulkRunning] = useState(false);
  const [bulkProgress, setBulkProgress] = useState({ done: 0, total: 0, errors: 0 });

  const fetchPending = useCallback(async ({ silent = false } = {}) => {
    // First-paint only sets `loading`; every subsequent fetch (silent or
    // user-initiated Refresh click) sets `isRefetching` so the existing
    // list stays visible with a small spinner — no blank-and-redraw.
    setIsRefetching(true);
    const startedAt = Date.now();
    try {
      const r = await fetch('/api/audio-lang/pending-review', {
        credentials: 'same-origin',
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
      setError(null);
      setLastRefreshedAt(Date.now());
    } catch (e) {
      // On refetch error, KEEP the stale data visible. The error banner
      // surfaces below the list. Only blank on first-paint failure.
      setError(e);
    } finally {
      // Minimum 350ms display so a sub-100ms fetch is still perceptible.
      // Below ~300ms the human eye registers the spinner as a flicker,
      // not a state change — they think the button didn't fire.
      const elapsed = Date.now() - startedAt;
      const padding = Math.max(0, 350 - elapsed);
      if (padding > 0) await new Promise(resolve => setTimeout(resolve, padding));
      setIsRefetching(false);
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPending();
    const onVerified = () => fetchPending({ silent: true });
    window.addEventListener('audio-lang-verified', onVerified);
    return () => window.removeEventListener('audio-lang-verified', onVerified);
  }, [fetchPending]);

  const openReview = useCallback((detail) => {
    window.dispatchEvent(new CustomEvent('open-audio-review', { detail }));
  }, []);

  const toggleEp = useCallback((id) => {
    setEpSelection((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

  const toggleSelectAll = useCallback((ids, select) => {
    setEpSelection((prev) => {
      const next = new Set(prev);
      for (const id of ids) {
        if (select) next.add(id); else next.delete(id);
      }
      return next;
    });
  }, []);

  const toggleExpand = useCallback((title) => {
    setExpandedSeries((prev) => {
      const next = new Set(prev);
      if (next.has(title)) next.delete(title); else next.add(title);
      return next;
    });
  }, []);

  const clearSelection = useCallback(() => setEpSelection(new Set()), []);

  // Filter + group by series.
  const { groups, totalCounts } = useMemo(() => {
    const allItems = data?.items || [];
    const counts = { all: allItems.length, suspect: 0, unknown: 0 };
    for (const it of allItems) {
      if (it.flag === 'suspect') counts.suspect += 1;
      else if (it.flag === 'unknown') counts.unknown += 1;
    }
    const s = search.trim().toLowerCase();
    const filtered = allItems.filter((it) => {
      if (filter !== 'all' && it.flag !== filter) return false;
      if (s) {
        const hay = `${it.title || ''} ${it.episode_number || ''} ${(it.file_canonical_path || it.canonical_path || '').toLowerCase()}`;
        if (!hay.toLowerCase().includes(s)) return false;
      }
      return true;
    });
    const byTitle = new Map();
    for (const it of filtered) {
      const t = it.title || '(unknown)';
      if (!byTitle.has(t)) {
        byTitle.set(t, {
          title: t,
          original_language: it.original_language,
          items: [],
        });
      }
      byTitle.get(t).items.push(it);
    }
    // Sort each series's episodes by episode_number, series alphabetical.
    for (const g of byTitle.values()) {
      g.items.sort((a, b) => {
        const an = a.episode_number || '';
        const bn = b.episode_number || '';
        return an.localeCompare(bn, undefined, { numeric: true });
      });
    }
    const groups = Array.from(byTitle.values()).sort((a, b) =>
      a.title.localeCompare(b.title)
    );
    return { groups, totalCounts: counts };
  }, [data, filter, search]);

  // Bulk action — fires one POST per file. Each one runs the full
  // verification pipeline (Sonarr propagation + Bazarr sync trigger), so
  // bulk-assigning Spanish to a whole telenovela closes the bazarr-blind
  // loop for every episode. Serial-ish; we cap concurrency at 4 so the
  // user gets fast feedback but we don't slam Sonarr.
  const applyBulk = useCallback(async () => {
    const paths = Array.from(epSelection);
    if (!paths.length) return;
    if (!window.confirm(
      `Assign "${bulkLang}" as the audio language for ${paths.length} file${paths.length === 1 ? '' : 's'}?`
      + `\n\nEach file will be saved locally, pushed to Sonarr's per-file language record, `
      + `and Bazarr will be triggered to re-sync. The selected rows will leave this list once verified.`
    )) return;
    setBulkRunning(true);
    setBulkProgress({ done: 0, total: paths.length, errors: 0 });
    let done = 0; let errors = 0;
    // Run 4 at a time. Each runs through the existing per-file endpoint
    // so propagation + Bazarr sync happens for every one.
    const queue = paths.slice();
    async function worker() {
      while (queue.length) {
        const p = queue.shift();
        try {
          const r = await fetch('/api/audio-lang/verifications', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({
              canonical_path: p,
              lang_code: bulkLang,
              source: 'user',
              confidence: 1.0,
              evidence: { bulk: true },
            }),
          });
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          // Dispatch the verified event so the list updates incrementally.
          window.dispatchEvent(new CustomEvent('audio-lang-verified', {
            detail: { file_canonical_path: p, lang_code: bulkLang },
          }));
        } catch (e) {
          // eslint-disable-next-line no-console
          console.error('bulk verify failed for', p, e);
          errors += 1;
        }
        done += 1;
        setBulkProgress({ done, total: paths.length, errors });
      }
    }
    await Promise.all([worker(), worker(), worker(), worker()]);
    setBulkRunning(false);
    clearSelection();
    // Refetch in case some verifies failed; ensures the list is honest.
    fetchPending({ silent: true });
  }, [epSelection, bulkLang, fetchPending, clearSelection]);

  const filterPills = [
    { id: 'all',     label: `all (${totalCounts.all})` },
    { id: 'suspect', label: `suspect (${totalCounts.suspect})` },
    { id: 'unknown', label: `unknown (${totalCounts.unknown})` },
  ];
  const selectedCount = epSelection.size;

  return (
    <main className="main-canvas" style={{
      padding: '22px 24px 22px', gap: 14, overflow: 'hidden',
      display: 'flex', flexDirection: 'column',
    }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 'var(--text-h1)', fontWeight: 600 }}>Review</h1>
          <div style={{ marginTop: 4, fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>
            Files where subarr wants you to confirm the audio language. Pick a series, tick whole shows
            for bulk assignment, or click 🎧 to listen first.
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {isRefetching && data && (
            <span aria-live="polite" style={{
              fontSize: 'var(--text-xs)', color: 'var(--fg-2)',
              display: 'inline-flex', alignItems: 'center', gap: 6,
            }}>
              <span className="spinner-inline" aria-hidden="true" style={{
                width: 10, height: 10, borderRadius: '50%',
                border: '2px solid var(--fg-3)',
                borderTopColor: 'transparent',
                animation: 'spin 0.8s linear infinite',
                display: 'inline-block',
              }} />
              Refreshing…
            </span>
          )}
          {/* "updated Xs ago" stamp — clicks Refresh button reset it to 0.
              Without this, the sub-100ms /api/audio-lang/pending-review
              response made the spinner invisible and the user thought the
              button was broken. */}
          {lastRefreshedAt > 0 && !isRefetching && (() => {
            const secs = Math.floor((nowTick - lastRefreshedAt) / 1000);
            const label = secs < 5 ? 'just now'
              : secs < 60 ? `${secs}s ago`
              : secs < 3600 ? `${Math.floor(secs / 60)}m ago`
              : `${Math.floor(secs / 3600)}h ago`;
            return (
              <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}
                title={`Last refreshed ${new Date(lastRefreshedAt).toLocaleTimeString()}`}>
                updated {label}
              </span>
            );
          })()}
          <button className="btn" onClick={() => fetchPending()} disabled={isRefetching}>
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </div>

      {/* Filters + search */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          height: 30, padding: '0 12px',
          background: 'var(--bg-2)', border: 'var(--border)',
          borderRadius: 'var(--radius-md)', width: 280,
        }}>
          <input type="search" value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search series or path…"
            aria-label="Search review items"
            style={{
              flex: 1, background: 'transparent', border: 'none',
              fontSize: 'var(--text-sm)', color: 'var(--fg-0)',
              outline: 'none',
            }} />
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          {filterPills.map((f) => (
            <span key={f.id} onClick={() => setFilter(f.id)}
              role="button" tabIndex={0}
              aria-label={`Filter by ${f.id}`}
              onKeyDown={(e) => { if (e.key === 'Enter') setFilter(f.id); }}
              className={`chip ${filter === f.id ? 'violet' : ''}`}
              style={{ cursor: 'pointer' }}>
              {f.label}
            </span>
          ))}
        </div>
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }} className="num">
          {groups.length} {groups.length === 1 ? 'series' : 'series'}, {groups.reduce((s, g) => s + g.items.length, 0)} files
        </span>
      </div>

      {/* List */}
      <div className="panel" style={{
        flex: 1, minHeight: 0, padding: 0, display: 'flex', flexDirection: 'column',
        overflow: 'hidden',
      }}>
        <div style={{
          display: 'grid',
          gridTemplateColumns: '14px 14px 1fr 80px 90px',
          alignItems: 'center', gap: 10,
          padding: '0 16px', height: 30,
          background: 'var(--bg-1)',
          borderBottom: 'var(--border)',
          fontSize: 'var(--text-2xs)',
          textTransform: 'uppercase', letterSpacing: '0.10em',
          color: 'var(--fg-2)',
          position: 'sticky', top: 0, zIndex: 2,
        }}>
          <span />
          <span />
          <span>series / file</span>
          <span>count</span>
          <span>selected</span>
        </div>
        <div style={{ flex: 1, overflow: 'auto' }}>
          {loading && !data && (
            <div style={{ padding: 40, textAlign: 'center', color: 'var(--fg-2)' }}>Loading review queue…</div>
          )}
          {error && !data && (
            <div style={{ padding: 40, textAlign: 'center', color: 'var(--error-500)' }}>
              Couldn't load: {String(error.message || error)}
              <div style={{ marginTop: 12 }}>
                <button className="btn" onClick={() => fetchPending()}>Retry</button>
              </div>
            </div>
          )}
          {data && groups.length === 0 && (
            <div style={{ padding: 40, textAlign: 'center', color: 'var(--fg-2)' }}>
              {totalCounts.all === 0
                ? "🎉 Nothing pending. Audio-language data looks clean across your library."
                : `No items match the "${filter}" filter${search ? ' or your search' : ''}.`}
            </div>
          )}
          {groups.map((g) => (
            <SeriesGroup
              key={g.title}
              series={g}
              expanded={expandedSeries.has(g.title)}
              onToggleExpand={() => toggleExpand(g.title)}
              epSelection={epSelection}
              onToggleSelectAll={toggleSelectAll}
              onToggleEp={toggleEp}
              onOpenEp={openReview}
            />
          ))}
        </div>
      </div>

      {/* Bulk action bar — sticky at the bottom when anything is selected. */}
      {selectedCount > 0 && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12,
          padding: 'var(--row-cozy)',
          background: 'var(--bg-2)',
          border: 'var(--border)',
          borderRadius: 'var(--radius-lg)',
          boxShadow: '0 -2px 12px rgba(0,0,0,0.25)',
        }}>
          <span style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-0)', fontWeight: 600 }}>
            {selectedCount} file{selectedCount === 1 ? '' : 's'} selected
          </span>
          <span style={{ color: 'var(--bg-5)' }}>·</span>
          <label style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
            Assign audio language
          </label>
          <select value={bulkLang}
                  onChange={(e) => setBulkLang(e.target.value)}
                  disabled={bulkRunning}
                  aria-label="Audio language to assign"
                  style={{
                    height: 28, padding: '0 8px',
                    background: 'var(--bg-1)', color: 'var(--fg-0)',
                    border: 'var(--border)', borderRadius: 'var(--radius-md)',
                    fontSize: 'var(--text-sm)',
                  }}>
            {LANG_PICKS.map(([code, name]) => (
              <option key={code} value={code}>{name} ({code})</option>
            ))}
          </select>
          {bulkRunning && (
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
              <span className="spinner-ring" style={{ marginRight: 6 }} />
              {bulkProgress.done} / {bulkProgress.total}
              {bulkProgress.errors > 0 && (
                <span style={{ color: 'var(--error-500)', marginLeft: 8 }}>
                  · {bulkProgress.errors} failed
                </span>
              )}
            </span>
          )}
          <span style={{ flex: 1 }} />
          <button className="btn ghost" onClick={clearSelection} disabled={bulkRunning}>
            Clear
          </button>
          <button className="btn primary" onClick={applyBulk} disabled={bulkRunning}>
            {bulkRunning ? 'Applying…' : `Apply to ${selectedCount}`}
          </button>
        </div>
      )}

      {/* Single-file modal — reused from Coverage. Listens for the
          open-audio-review event dispatched by the 🎧 button. */}
      <AudioReviewModal />
    </main>
  );
}
