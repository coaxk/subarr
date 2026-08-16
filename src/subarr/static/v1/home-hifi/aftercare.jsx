// #156 Track A: aftercare review page. Mirrors health.jsx (poll + row list +
// expand). Leads with failure flags; never presents a confident positive grade
// (accuracy score is L3/#123). Clean jobs read "no problems detected".

import { StatusDot, LibraryChip } from './atoms.jsx';
import { AudioReviewModal } from './coverage.jsx';
import { BlacklistPanel } from './blacklist-panel.jsx';
// P4: reuse Review's pagination derivation (server `count` -> prev/next + range).
import { computePagination } from './review.jsx';

const { useState, useEffect, useCallback, useRef } = React;

// ISO-639-1 → representative country flag. Languages aren't countries, so a few
// (en/es/pt/ca) pick the most common flag; unknown falls back to a white flag.
const LANG_FLAG = {
  en: '🇬🇧', es: '🇪🇸', fr: '🇫🇷', de: '🇩🇪', it: '🇮🇹', pt: '🇵🇹', nl: '🇳🇱',
  ru: '🇷🇺', uk: '🇺🇦', pl: '🇵🇱', cs: '🇨🇿', sk: '🇸🇰', hr: '🇭🇷', sr: '🇷🇸',
  bg: '🇧🇬', sl: '🇸🇮', el: '🇬🇷', tr: '🇹🇷', he: '🇮🇱', ar: '🇸🇦', fa: '🇮🇷',
  hi: '🇮🇳', ko: '🇰🇷', ja: '🇯🇵', zh: '🇨🇳', th: '🇹🇭', vi: '🇻🇳', id: '🇮🇩',
  ro: '🇷🇴', hu: '🇭🇺', ca: '🇪🇸', sv: '🇸🇪', no: '🇳🇴', nn: '🇳🇴', da: '🇩🇰',
  fi: '🇫🇮', is: '🇮🇸',
};
function langFlag(code) {
  return code ? (LANG_FLAG[code] || '🏳️') : '';
}

function badgeKind(item) {
  if (!item.flagged) return 'ok';
  const crit = (item.readability?.issues || []).some(i => i.severity === 'critical');
  if (item.composite < 50 || crit || (item.signals?.canned_phrase_hits || 0) > 0
      || (item.signals?.ad_boilerplate_hits || 0) > 0) return 'error';
  return 'warn';
}

function flagChips(item) {
  const out = [];
  const s = item.signals || {}, c = (item.readability || {}).counts || {};
  // Only flag repeats once they cross the backend's failure threshold
  // (AFTERCARE_REPEAT_MAX = 0.20). Below that a stray repeated line is normal,
  // so showing "1% repeats" — or a sub-0.5% ratio that rounds to "0%" — as a
  // red chip was a false alarm that disagreed with the score.
  if ((s.repeated_line_ratio || 0) > 0.20) out.push(`${Math.round(s.repeated_line_ratio * 100)}% repeats`);
  if ((s.canned_phrase_hits || 0) > 0) out.push(`${s.canned_phrase_hits} canned`);
  if ((s.ad_boilerplate_hits || 0) > 0) out.push(`${s.ad_boilerplate_hits} ad/boilerplate`);
  if ((s.sync_overrun_s || 0) > 30) out.push(`${Math.round(s.sync_overrun_s)}s overrun`);
  if (c.cps) out.push(`${c.cps} CPS issue${c.cps === 1 ? '' : 's'}`);
  if (c.overlap) out.push(`${c.overlap} overlap`);
  return out;
}

// Per-component breakdown shown on hover over the score (the "why this
// number" the chips only hint at). Reads the values already on the row —
// no scoring math is reconstructed here, so it can't drift from the
// backend; clean dimensions are shown too ("0% repeats") so the user sees
// what's NOT wrong, not just what is.
function scoreBreakdown(item) {
  const s = item.signals || {};
  const c = (item.readability || {}).counts || {};
  const sig = [
    `looping/repeats ${Math.round((s.repeated_line_ratio || 0) * 100)}%`,
    `hallucination (canned) ${s.canned_phrase_hits || 0}`,
  ];
  if (s.ad_boilerplate_hits != null) sig.push(`ad/boilerplate ${s.ad_boilerplate_hits || 0}`);
  if (s.sync_overrun_s != null) sig.push(`sync overrun ${Math.round(s.sync_overrun_s || 0)}s`);
  const read = [];
  if (c.cps) read.push(`${c.cps} CPS`);
  if (c.too_long) read.push(`${c.too_long} too-long`);
  if (c.too_short) read.push(`${c.too_short} too-short`);
  if (c.lines) read.push(`${c.lines} over-2-line`);
  if (c.cpl) read.push(`${c.cpl} long-line`);
  if (c.overlap) read.push(`${c.overlap} overlap`);
  return [
    `Structural score ${Math.round(item.composite)} — not a transcription-accuracy grade.`,
    '',
    `Failure signals: ${sig.join(' · ')}`,
    `Readability: ${read.length ? read.join(', ') : 'clean'}`,
  ].join('\n');
}

function timeAgo(ts) {
  if (!ts) return 'never';
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function ItemRow({ item, expanded, onToggleExpand, busy, onAcknowledge, onRequeue, onListen }) {
  const kind = badgeKind(item);
  const chips = item.flagged ? flagChips(item) : [];
  const filename = item.canonical_path.split('/').slice(-1)[0];
  const isBusy = busy === item.id;

  return (
    <React.Fragment>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px',
        borderRadius: 'var(--radius-md)',
        background: item.flagged
          ? (kind === 'error' ? 'rgba(239,68,68,0.06)' : 'rgba(245,158,11,0.06)')
          : 'transparent',
      }}>
        {/* Status dot */}
        <span style={{ flex: 'none' }}>
          <StatusDot kind={kind} />
        </span>

        {/* Country flag + language code (the tuning axis) */}
        {item.language && (
          <span title={`audio language: ${item.language}`}
            style={{ flex: 'none', display: 'inline-flex', alignItems: 'center', gap: 4, width: 46 }}>
            <span style={{ fontSize: 'var(--text-sm)' }}>{langFlag(item.language)}</span>
            <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)', textTransform: 'uppercase' }}>
              {item.language}
            </span>
          </span>
        )}

        {/* Filename + flag chips */}
        <span style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 8, overflow: 'hidden' }}>
          <span
            title={item.canonical_path}
            style={{
              fontWeight: item.flagged ? 600 : 400,
              color: item.flagged ? 'var(--fg-0)' : 'var(--fg-2)',
              fontSize: 'var(--text-sm)',
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0,
            }}>
            {filename}
          </span>
          <LibraryChip library={item.library} />
          {item.flagged && chips.length > 0 && chips.map((chip, i) => (
            <span key={i} className="chip"
              style={{ fontSize: 'var(--text-2xs)', flex: 'none',
                color: kind === 'error' ? 'var(--error-500)' : 'var(--warn-500)' }}>
              {chip}
            </span>
          ))}
          {!item.flagged && (
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)', fontStyle: 'italic' }}>
              no problems detected
            </span>
          )}
        </span>

        {/* Source tag */}
        {item.source && (
          <span className="chip" title="how this job was queued"
            style={{ flex: 'none', fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>
            {item.source}
          </span>
        )}

        {/* Composite (structural — muted, flagged only). Hover = the
            per-component breakdown borrowed from the competitive review. */}
        {item.flagged && (
          <span title={scoreBreakdown(item)}
            style={{ flex: 'none', fontSize: 'var(--text-2xs)', color: 'var(--fg-3)',
              fontVariantNumeric: 'tabular-nums', width: 26, textAlign: 'right', cursor: 'help' }}>
            {Math.round(item.composite)}
          </span>
        )}

        {/* Completed timestamp */}
        <span style={{ flex: 'none', fontSize: 'var(--text-sm)', color: 'var(--fg-3)', width: 90, textAlign: 'right' }}
          title="Completed">
          {timeAgo(item.completed_at)}
        </span>

        {/* Action buttons */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flex: 'none' }}>
          <button
            onClick={() => onListen(item)}
            disabled={isBusy}
            title="Listen to audio"
            className="btn ghost sm"
            style={{ padding: '0 6px', fontSize: 'var(--text-sm)' }}>
            🎧
          </button>
          <button
            onClick={() => onAcknowledge(item)}
            disabled={isBusy}
            className="btn sm"
            style={{ fontSize: 'var(--text-xs)' }}>
            {isBusy ? '…' : 'Acknowledge'}
          </button>
          <button
            onClick={() => onRequeue(item)}
            disabled={isBusy}
            title={item.source === 'existing_audit'
              ? 'Regenerate this subtitle from the audio (subgen)'
              : 'Send the file back to subgen with the same config'}
            className="btn sm"
            style={{ fontSize: 'var(--text-xs)' }}>
            {isBusy ? '…' : (item.source === 'existing_audit' ? 'Regenerate' : 'Requeue')}
          </button>
          {/* #165: requeue re-runs the SAME config and often reproduces the
              same junk — this hands the file to the Tuning Lab to find a
              better one. Deep-link via query params (arena pre-seeds from
              ?path=/&lang= on mount). */}
          <button
            onClick={() => {
              const q = new URLSearchParams({ path: item.canonical_path });
              if (item.language) q.set('lang', item.language);
              window.location.href = `/arena?${q.toString()}`;
            }}
            disabled={isBusy}
            title="Open the Tuning Lab pre-loaded with this file to compare recipes"
            className="btn sm"
            style={{ fontSize: 'var(--text-xs)' }}>
            Find a better config
          </button>
          {/* #317: external (provider) subs can be blacklisted in Bazarr so it
              stops re-fetching the bad release. Opens the history panel for this
              file; only meaningful for audited externals, not our subgen output. */}
          {item.source === 'existing_audit' && (
            <button
              onClick={() => window.dispatchEvent(new CustomEvent('open-blacklist', {
                detail: { path: item.canonical_path, title: filename },
              }))}
              disabled={isBusy}
              title="Blacklist this provider's subtitle in Bazarr (stops re-fetching the bad release)"
              className="btn sm"
              style={{ fontSize: 'var(--text-xs)' }}>
              Blacklist
            </button>
          )}
          <span
            onClick={() => onToggleExpand(item.id)}
            role="button" tabIndex={0}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onToggleExpand(item.id); }}
            style={{ width: 18, textAlign: 'center', cursor: 'pointer', color: 'var(--fg-3)', userSelect: 'none' }}>
            {expanded ? '▾' : '▸'}
          </span>
        </div>
      </div>

      {/* #216: sanitized snippet of a representative cue — shows the actual
          junk (scene-release ad, gibberish) for an audited external sub. */}
      {item.preview && (
        <div title={item.preview}
          style={{ margin: '-2px 0 6px 44px', fontSize: 'var(--text-xs)',
            color: 'var(--fg-3)', fontStyle: 'italic',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          “{item.preview}”
        </div>
      )}

      {expanded && (
        <div style={{
          margin: '0 0 8px 28px', padding: '10px 14px',
          background: '#0d0d10', border: 'var(--border)',
          borderRadius: 'var(--radius-md)',
        }}>
          {/* Issues list */}
          {item.readability?.issues?.length > 0 ? (
            <div style={{ marginBottom: 10 }}>
              <div style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>
                Readability issues
              </div>
              {item.readability.issues.map((issue, i) => (
                <div key={i} style={{
                  fontFamily: 'JetBrains Mono, monospace', fontSize: 11, lineHeight: 1.6,
                  color: issue.severity === 'critical' ? 'var(--error-500)' : 'var(--fg-2)',
                  padding: '1px 0',
                }}>
                  #{issue.cue} {issue.kind}/{issue.severity}: {issue.detail}
                </div>
              ))}
            </div>
          ) : item.flagged ? (
            <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)', marginBottom: 10 }}>
              Flagged by signals — no per-cue issues recorded.
            </div>
          ) : null}

          {/* Footer — structural score, NOT accuracy */}
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)', borderTop: '1px solid var(--bg-3)', paddingTop: 8 }}>
            <span style={{ color: 'var(--fg-2)' }}>composite {item.composite}</span>
            {' '}
            <span style={{ color: 'var(--fg-3)', fontStyle: 'italic', fontSize: 'var(--text-2xs)' }}>
              (structural — not accuracy)
            </span>
            {' · '}
            {item.cue_count} cue{item.cue_count === 1 ? '' : 's'}
            {' · '}
            {item.source}
            {item.reviewed_at && (
              <span style={{ marginLeft: 8 }}>· reviewed {timeAgo(item.reviewed_at)}</span>
            )}
          </div>
        </div>
      )}
    </React.Fragment>
  );
}

// Collapsible legend explaining the status dots + flag chips. Errors here are
// the deterministic failure-modes the judges detect — not accuracy.
// P4: build the /api/aftercare/results query string for server-side search +
// page slicing + view/source filters. The server validates these (search
// max_length=200, limit 1..500, offset >= 0) and returns the truthful total
// matching `count` for the CURRENT query. Empty search is omitted so the URL
// stays clean. Exported for tests, mirroring review.jsx's buildReviewQuery.
export function buildAftercareQuery({ view = 'flagged', source = null, search = '', limit = 100, offset = 0 }) {
  const q = new URLSearchParams();
  q.set('view', view);
  if (source) q.set('source', source);
  const s = (search || '').trim();
  if (s) q.set('search', s);
  q.set('limit', String(limit));
  q.set('offset', String(offset));
  return q.toString();
}

// P4-S4: pure predicate for the empty-last-page recovery. If a response comes
// back with no rows but the server reports more matching rows beyond this page
// (rows were acknowledged / requeued / audited out between fetches), the page
// is now past the end and invalid. Returns the offset to step back to (the
// previous page start), or null when there is genuinely no match (render the
// empty state as-is). Exported for tests.
export function recoverEmptyPageOffset({ itemsLength = 0, count = 0, offset = 0, limit = 100 }) {
  if (itemsLength === 0 && count > 0 && offset > 0) {
    return Math.max(0, offset - limit);
  }
  return null;
}

function Legend() {
  const dotItem = (kind, label, desc) => (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <StatusDot kind={kind} />
      <span style={{ color: 'var(--fg-1)', fontWeight: 600 }}>{label}</span>
      <span style={{ color: 'var(--fg-3)' }}>— {desc}</span>
    </span>
  );
  const chipItem = (label, color, desc) => (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span className="chip" style={{ fontSize: 'var(--text-2xs)', flex: 'none', color }}>{label}</span>
      <span style={{ color: 'var(--fg-3)' }}>— {desc}</span>
    </span>
  );
  return (
    <details style={{ fontSize: 'var(--text-xs)' }}>
      <summary style={{ cursor: 'pointer', color: 'var(--fg-2)', userSelect: 'none' }}>
        What do the flags mean?
      </summary>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: '12px 4px 4px' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px 24px' }}>
          {dotItem('error', 'serious', 'score under 50, a critical readability issue, or a hallucination')}
          {dotItem('warn', 'flagged', 'a structural problem worth a look')}
          {dotItem('ok', 'clean', 'no problems detected')}
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px 24px' }}>
          {chipItem('repeats', 'var(--error-500)', 'looping — the same line repeated (Whisper got stuck)')}
          {chipItem('canned', 'var(--error-500)', 'hallucinated boilerplate, e.g. "Thanks for watching" over silence')}
          {chipItem('CPS issues', 'var(--warn-500)', 'reading speed too fast — characters-per-second over the limit')}
          {chipItem('overlap', 'var(--warn-500)', 'cues overlap in time — two subtitles on screen at once')}
        </div>
        <div style={{ color: 'var(--fg-3)', fontStyle: 'italic' }}>
          These are deterministic failure-mode checks, not a translation-accuracy grade.
        </div>
      </div>
    </details>
  );
}

export function AftercarePage() {
  const [view, setView] = useState('flagged');
  const [source, setSource] = useState(null); // null = all sources; 'existing_audit' = audited externals
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(null);
  const [expandedId, setExpandedId] = useState(null);
  const [audit, setAudit] = useState({ running: false, done: 0, total: 0 }); // #216 audit progress
  const [ackingAll, setAckingAll] = useState(false); // #313 bulk-acknowledge in flight

  // P4: server-side search + pagination. `search` is the live input value;
  // `debouncedSearch` is the term that actually drives the request (see the
  // debounce effect below). `limit` is the page size (matches the server
  // default 100) and `offset` the current page's starting row into the server's
  // searched set.
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [limit, setLimit] = useState(100);
  const [offset, setOffset] = useState(0);
  // P4-S3: a request sequence token so a stale poll / query response can never
  // overwrite a newer one — the fetch guard in refetch. isRefetching disables
  // the pagination buttons mid-flight so a pending response can't be double-
  // stepped past the end (same convention as review.jsx).
  const fetchSeq = useRef(0);
  const [isRefetching, setIsRefetching] = useState(false);

  // P4: debounce the server search so typing doesn't fire a request per
  // keystroke. When the term settles, reset to page 1 (a new search starts at
  // the top of the results).
  useEffect(() => {
    const id = setTimeout(() => {
      setDebouncedSearch(search);
      setOffset(0);
    }, 250);
    return () => clearTimeout(id);
  }, [search]);

  const refetch = useCallback(async () => {
    // P4-S3: claim a fresh sequence token; a stale poll or older query/page
    // that resolves later must not clobber the newer one.
    const seq = ++fetchSeq.current;
    setIsRefetching(true);
    try {
      const q = buildAftercareQuery({ view, source, search: debouncedSearch, limit, offset });
      const r = await fetch(`/api/aftercare/results?${q}`, { credentials: 'same-origin' });
      if (!r.ok) return;
      const payload = await r.json();
      const items = payload?.items || [];
      // P4-S2: empty-last-page recovery — after an acknowledge / requeue / audit
      // completion rows may have left the current page so it's now past the end.
      // Step back to the previous valid page and refetch instead of leaving a
      // blank invalid page (the offset change below re-triggers refetch).
      const stepBack = recoverEmptyPageOffset({ itemsLength: items.length, count: payload?.total, offset, limit });
      if (stepBack !== null) {
        if (seq === fetchSeq.current) setOffset(stepBack);
        return;
      }
      if (seq !== fetchSeq.current) return; // a newer query/page won the race
      setData(payload);
    } finally {
      if (seq === fetchSeq.current) setIsRefetching(false);
    }
  }, [view, source, debouncedSearch, limit, offset]);

  useEffect(() => {
    refetch();
    const id = setInterval(refetch, 8000);
    return () => clearInterval(id);
  }, [refetch]);

  // #216: existing-subtitle audit. Single-flight on the server; here we POST to
  // start, then poll status until it finishes and refetch the (now-populated)
  // results. On mount we read status once so the button reflects a run already
  // in flight (e.g. started in another tab).
  const pollAudit = useCallback(async () => {
    const s = await fetch('/api/aftercare/audit/status', { credentials: 'same-origin' });
    if (!s.ok) return;
    const st = await s.json();
    setAudit({ running: !!st.running, done: st.done || 0, total: st.total || 0 });
    if (st.running) setTimeout(pollAudit, 1500);
    else refetch();
  }, [refetch]);

  useEffect(() => { pollAudit(); }, [pollAudit]);

  const runAudit = useCallback(async () => {
    setAudit((a) => ({ ...a, running: true }));
    await fetch('/api/aftercare/audit', { method: 'POST', credentials: 'same-origin' });
    setTimeout(pollAudit, 800);
  }, [pollAudit]);

  const acknowledge = useCallback(async (item) => {
    setBusy(item.id);
    try { await fetch(`/api/aftercare/${item.id}/acknowledge`, { method: 'POST', credentials: 'same-origin' }); }
    finally { setBusy(null); refetch(); }
  }, [refetch]);

  // #313: clear a large first-run backlog in one action. Acks every pending
  // item (respecting the source filter). Does NOT touch the subtitle files.
  const acknowledgeAll = useCallback(async () => {
    const scope = source === 'existing_audit' ? 'audited external' : 'pending';
    if (!window.confirm(`Mark all ${scope} aftercare items as reviewed? This clears the review list. The subtitle files are not changed.`)) return;
    setAckingAll(true);
    try {
      const q = source ? `?source=${encodeURIComponent(source)}` : '';
      await fetch(`/api/aftercare/acknowledge-all${q}`, { method: 'POST', credentials: 'same-origin' });
    } finally { setAckingAll(false); refetch(); }
  }, [source, refetch]);

  // Requeue for our own jobs; for audited EXTERNAL subs, regenerate-from-audio
  // (the server resolves the sibling video — the .srt path can't be transcribed).
  const requeue = useCallback(async (item) => {
    setBusy(item.id);
    try {
      if (item.source === 'existing_audit') {
        await fetch(`/api/aftercare/${item.id}/regenerate`, { method: 'POST', credentials: 'same-origin' });
      } else {
        await fetch('/api/queue/requeue', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin', body: JSON.stringify({ path: item.canonical_path }),
        });
      }
      await fetch(`/api/aftercare/${item.id}/acknowledge`, { method: 'POST', credentials: 'same-origin' });
    } finally { setBusy(null); refetch(); }
  }, [refetch]);

  const listen = useCallback((item) => {
    window.dispatchEvent(new CustomEvent('open-audio-review', {
      detail: { title: item.canonical_path.split('/').slice(-1)[0],
                _canonical_path: item.canonical_path },
    }));
  }, []);

  const toggleExpand = useCallback((id) => {
    setExpandedId((prev) => (prev === id ? null : id));
  }, []);

  // P4-S1: the server's `total` is the TRUTHFUL total matching the current query
  // (view + source + search); `count` remains the length of the returned page. Only the
  // ACTIVE view's total is in this response, so only its pill shows a number;
  // the inactive view's total isn't derivable from a single page, so it shows
  // the bare label rather than a misleading page-length count.
  const totalCount = data ? data.total : null;

  const viewPills = [
    { id: 'flagged', label: (view === 'flagged' && totalCount !== null) ? `flagged (${totalCount})` : 'flagged' },
    { id: 'all',     label: (view === 'all' && totalCount !== null)     ? `all (${totalCount})`     : 'all'     },
  ];

  const items = data?.items || [];
  // P4-S1: pagination facts from the server's truthful total (`total`).
  const pagination = computePagination({ count: data?.total, limit, offset });

  return (
    <main className="main-canvas" style={{ padding: '22px 24px 22px', gap: 14, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>

      {/* Page header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 'var(--text-h1)', fontWeight: 600 }}>Aftercare</h1>
          <div style={{ marginTop: 4, fontSize: 'var(--text-sm)', color: 'var(--fg-2)', maxWidth: 760 }}>
            Post-transcription quality review for completed jobs. Structural checks (repeats, canned phrases, CPS, overlap) flag
            subtitles that may need a re-run. Accuracy scoring requires the tuning lab — this page only surfaces what can be
            detected automatically.
          </div>
        </div>
        {totalCount > 0 && (
          <button onClick={acknowledgeAll} disabled={ackingAll} className="btn sm"
            title="Mark all pending items reviewed — clears the backlog in one action. Does not change the subtitle files."
            style={{ flex: 'none', whiteSpace: 'nowrap' }}>
            {ackingAll ? 'Acknowledging…' : 'Acknowledge all'}
          </button>
        )}
      </div>

      {/* View toggle + source filter + existing-sub audit trigger (#216) */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        {viewPills.map((p) => (
          <span key={p.id} onClick={() => { setView(p.id); setOffset(0); }}
            role="button" tabIndex={0}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { setView(p.id); setOffset(0); } }}
            className={`chip ${view === p.id ? 'violet' : ''}`}
            style={{ cursor: 'pointer' }}>
            {p.label}
          </span>
        ))}
        <span style={{ width: 1, height: 16, background: 'var(--bg-3)', margin: '0 2px' }} />
        {[{ id: null, label: 'all sources' }, { id: 'existing_audit', label: 'existing audit' }].map((p) => (
          <span key={p.id || 'all'} onClick={() => { setSource(p.id); setOffset(0); }}
            role="button" tabIndex={0}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { setSource(p.id); setOffset(0); } }}
            className={`chip ${source === p.id ? 'violet' : ''}`}
            style={{ cursor: 'pointer' }}>
            {p.label}
          </span>
        ))}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          height: 30, padding: '0 12px',
          background: 'var(--bg-2)', border: 'var(--border)',
          borderRadius: 'var(--radius-md)', width: 280,
        }}>
          <input type="search" value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search path, source, or preview…"
            aria-label="Search aftercare items"
            style={{
              flex: 1, background: 'transparent', border: 'none',
              fontSize: 'var(--text-sm)', color: 'var(--fg-0)',
              outline: 'none',
            }} />
        </div>
        <span style={{ flex: 1 }} />
        <button onClick={runAudit} disabled={audit.running}
          title="Scan the external subtitles you already have and score their quality"
          className="btn sm" style={{ fontSize: 'var(--text-xs)' }}>
          {audit.running
            ? `Auditing…${audit.total ? ` ${audit.done}/${audit.total}` : ''}`
            : 'Audit existing subtitles'}
        </button>
        <Legend />
      </div>

      {/* P4: server-side pagination — total from the server `total`, prev/next
          disabled at the boundaries and mid-refetch, and a page-size selector.
          Changing the page size resets to the first page. Mirrors review.jsx. */}
      {data && pagination.total > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '2px 2px 6px' }}>
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>
            {pagination.shownStart}–{pagination.shownEnd} of {pagination.total}
          </span>
          <label style={{
            display: 'inline-flex', alignItems: 'center', gap: 6,
            fontSize: 'var(--text-xs)', color: 'var(--fg-3)',
          }}>
            Page size
            <select value={limit}
              onChange={(e) => { setLimit(Number(e.target.value)); setOffset(0); }}
              aria-label="Page size"
              style={{
                height: 24, padding: '0 6px', background: 'var(--bg-1)', color: 'var(--fg-0)',
                border: 'var(--border)', borderRadius: 'var(--radius-md)', fontSize: 'var(--text-2xs)',
              }}>
              {[50, 100, 200, 500].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <span style={{ flex: 1 }} />
          <button className="btn sm" disabled={!pagination.hasPrev || isRefetching}
            aria-label="Previous page"
            onClick={() => setOffset(Math.max(0, offset - limit))}>
            ‹ Prev
          </button>
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
            Page {pagination.pageNumber} of {pagination.totalPages}
          </span>
          <button className="btn sm" disabled={!pagination.hasNext || isRefetching}
            aria-label="Next page"
            onClick={() => setOffset(offset + limit)}>
            Next ›
          </button>
        </div>
      )}

      {/* Content card */}
      <section style={cardStyle}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
          <span className="label">Completed jobs</span>
          <span style={{ flex: 1 }} />
          <span style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-3)' }}>
            {data == null
              ? 'loading…'
              : view === 'flagged'
                ? (totalCount === 0 ? 'nothing flagged' : `${totalCount} flagged`)
                : `${totalCount} total`}
          </span>
        </div>

        {/* Loading state */}
        {data === null && (
          <div style={{ padding: '24px 12px', color: 'var(--fg-2)', fontSize: 'var(--text-sm)' }}>
            Loading…
          </div>
        )}

        {/* Empty state */}
        {data !== null && items.length === 0 && (
          <div style={{ padding: '24px 12px', color: 'var(--fg-3)', fontSize: 'var(--text-sm)' }}>
            {debouncedSearch.trim()
              ? `${view === 'flagged' ? 'No flagged jobs' : 'No completed jobs'} match your search “${debouncedSearch.trim()}”.`
              : view === 'flagged'
                ? 'Nothing needs review — no flagged jobs at the moment.'
                : 'No completed jobs recorded yet. Jobs appear here once subgen finishes processing them.'}
          </div>
        )}

        {/* Row list */}
        {items.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            {items.map((item) => (
              <ItemRow
                key={item.id}
                item={item}
                expanded={expandedId === item.id}
                onToggleExpand={toggleExpand}
                busy={busy}
                onAcknowledge={acknowledge}
                onRequeue={requeue}
                onListen={listen}
              />
            ))}
          </div>
        )}

        {data !== null && items.length > 0 && (
          <div style={{ marginTop: 8, fontSize: 'var(--text-sm)', color: 'var(--fg-3)' }}>
            Acknowledge to dismiss. Requeue sends the file back to subgen and dismisses this entry.
            Composite score is structural only — not a transcription accuracy grade.
          </div>
        )}
      </section>

      {/* Single-file audio modal — reused from Coverage. Listens for the
          open-audio-review event dispatched by the 🎧 button. */}
      <AudioReviewModal />
      <BlacklistPanel />
    </main>
  );
}

const cardStyle = {
  background: 'var(--bg-1)', border: 'var(--border)',
  borderRadius: 'var(--radius-lg)', padding: '16px 18px',
};
