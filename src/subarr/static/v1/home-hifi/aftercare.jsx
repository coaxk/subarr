// #156 Track A: aftercare review page. Mirrors health.jsx (poll + row list +
// expand). Leads with failure flags; never presents a confident positive grade
// (accuracy score is L3/#123). Clean jobs read "no problems detected".

import { StatusDot } from './atoms.jsx';
import { AudioReviewModal } from './coverage.jsx';

const { useState, useEffect, useCallback } = React;

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
  if (item.composite < 50 || crit || (item.signals?.canned_phrase_hits || 0) > 0) return 'error';
  return 'warn';
}

function flagChips(item) {
  const out = [];
  const s = item.signals || {}, c = (item.readability || {}).counts || {};
  if ((s.repeated_line_ratio || 0) > 0) out.push(`${Math.round(s.repeated_line_ratio * 100)}% repeats`);
  if ((s.canned_phrase_hits || 0) > 0) out.push(`${s.canned_phrase_hits} canned`);
  if (c.cps) out.push(`${c.cps} CPS issue${c.cps === 1 ? '' : 's'}`);
  if (c.overlap) out.push(`${c.overlap} overlap`);
  return out;
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

        {/* Composite (structural — muted, flagged only) */}
        {item.flagged && (
          <span title="structural score — not a transcription-accuracy grade"
            style={{ flex: 'none', fontSize: 'var(--text-2xs)', color: 'var(--fg-3)',
              fontVariantNumeric: 'tabular-nums', width: 26, textAlign: 'right' }}>
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
            className="btn sm"
            style={{ fontSize: 'var(--text-xs)' }}>
            {isBusy ? '…' : 'Requeue'}
          </button>
          <span
            onClick={() => onToggleExpand(item.id)}
            role="button" tabIndex={0}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onToggleExpand(item.id); }}
            style={{ width: 18, textAlign: 'center', cursor: 'pointer', color: 'var(--fg-3)', userSelect: 'none' }}>
            {expanded ? '▾' : '▸'}
          </span>
        </div>
      </div>

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
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(null);
  const [expandedId, setExpandedId] = useState(null);

  const refetch = useCallback(async () => {
    const r = await fetch(`/api/aftercare/results?view=${view}`, { credentials: 'same-origin' });
    if (r.ok) setData(await r.json());
  }, [view]);

  useEffect(() => {
    refetch();
    const id = setInterval(refetch, 8000);
    return () => clearInterval(id);
  }, [refetch]);

  const acknowledge = useCallback(async (item) => {
    setBusy(item.id);
    try { await fetch(`/api/aftercare/${item.id}/acknowledge`, { method: 'POST', credentials: 'same-origin' }); }
    finally { setBusy(null); refetch(); }
  }, [refetch]);

  const requeue = useCallback(async (item) => {
    setBusy(item.id);
    try {
      await fetch('/api/queue/requeue', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin', body: JSON.stringify({ path: item.canonical_path }),
      });
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

  // View toggle pills. Show count if data is loaded.
  const flaggedCount = data ? data.items.filter(i => i.flagged).length : null;
  const allCount = data ? data.items.length : null;

  const viewPills = [
    { id: 'flagged', label: flaggedCount !== null ? `flagged (${flaggedCount})` : 'flagged' },
    { id: 'all',     label: allCount !== null     ? `all (${allCount})`         : 'all'     },
  ];

  const items = data?.items || [];

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
      </div>

      {/* View toggle */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {viewPills.map((p) => (
          <span key={p.id} onClick={() => setView(p.id)}
            role="button" tabIndex={0}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setView(p.id); }}
            className={`chip ${view === p.id ? 'violet' : ''}`}
            style={{ cursor: 'pointer' }}>
            {p.label}
          </span>
        ))}
        <span style={{ flex: 1 }} />
        <Legend />
      </div>

      {/* Content card */}
      <section style={cardStyle}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
          <span className="label">Completed jobs</span>
          <span style={{ flex: 1 }} />
          <span style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-3)' }}>
            {data == null
              ? 'loading…'
              : view === 'flagged'
                ? (flaggedCount === 0 ? 'nothing flagged' : `${flaggedCount} flagged`)
                : `${allCount} total`}
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
            {view === 'flagged'
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
    </main>
  );
}

const cardStyle = {
  background: 'var(--bg-1)', border: 'var(--border)',
  borderRadius: 'var(--radius-lg)', padding: '16px 18px',
};
