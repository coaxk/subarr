// v1.1.1: dedicated audio-language review queue page.
//
// Tree-grouped by series with bulk assignment: user picks a target
// language, ticks one or more series (or individual episodes), and
// applies the verification across the selection in one click. Each
// individual verify still runs through the full pipeline — local
// audio_lang_store + Sonarr propagation + Bazarr sync trigger — so
// bulk-assigning Spanish to a whole telenovela closes the bazarr-blind
// loop for every episode at once.

import { StatusDot, LibraryChip } from './atoms.jsx';
import { AudioReviewModal } from './coverage.jsx';
import { distinctSeriesPrefixes } from './lang-rules-util.mjs';
import { useLanguagePicks } from './languages.mjs';

const { useState, useEffect, useCallback, useMemo } = React;

function FlagDot({ flag }) {
  // #406: multilingual rows are auto-detected multi-language files — badge them
  // with a distinct 🌐 chip, not the suspect/unknown status dot.
  if (flag === 'multilingual') {
    return (
      <span title="Auto-detected multilingual audio — review or accept as detected"
        aria-label="multilingual"
        style={{ fontSize: 'var(--text-2xs)' }}>🌐</span>
    );
  }
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

// #159: a default-audio-track mismatch row — distinct from the language-verify
// rows. The fix isn't "pick a language" (we already know it); it's a one-click
// in-place swap of the default audio track to the show's original language, so
// subgen stops transcribing a dub into double-translated subs.
function TrackMismatchRow({ item, selected, onToggle, busy, onSwap, onDismiss, onOpen }) {
  const path = item.file_canonical_path || item.canonical_path;
  const def = (item.mismatch_default_track_lang || '?').toUpperCase();
  const native = (item.mismatch_native_track_lang || item.original_language || '?').toUpperCase();
  const ord = item.mismatch_native_audio_ordinal;
  const why = `This is a ${item.original_language || native} title, but the default audio `
    + `track is ${def}. Transcribing the default double-translates the dub `
    + `(${native}→${def}→subtitle) and loses fidelity. The original ${native} audio is `
    + `track a${ord}. Make it the default? (in-place, lossless, reversible)`;
  const detail = {
    title: item.title, ep: item.episode_number || '',
    _canonical_path: path, audio: (item.audio_langs || []).join(',') || 'und',
    original_language: item.original_language,
    audio_label_notes: item.notes ? [item.notes] : [],
    // #159 UX: on a track-mismatch row the fix is "swap the default track", NOT
    // "confirm a language" — so the 🎧 modal opens listen-only (no language
    // picker that does nothing here and traps the user into a no-op confirm).
    listen_only: true,
  };
  const explainer = `Default audio is the ${def} track (a dub). This is a `
    + `${item.original_language || native} title — set the original ${native} track (a${ord}) as `
    + `default so subtitles transcribe from the source, not a dub-of-a-dub. In-place, lossless, reversible.`;
  return (
    <div role="row" data-testid="review-row-mismatch" style={{
      display: 'flex', flexDirection: 'column', gap: 4,
      padding: '8px 16px 8px 32px',
      borderBottom: '1px solid var(--bg-3)',
      background: selected ? 'rgba(245,158,11,0.12)' : 'rgba(245,158,11,0.05)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <CheckBox checked={selected}
                  onChange={onToggle}
                  label={`Select ${item.title} ${item.episode_number || ''}`} />
        <span aria-label="default audio track mismatch"
          style={{ fontSize: 'var(--text-sm)' }}>⇄</span>
        <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-2)', width: 70 }}>
          {item.episode_number || '—'}
        </span>
        <span className="mono" title={path} style={{
          fontSize: 'var(--text-2xs)', color: 'var(--fg-3)', flex: 1, minWidth: 0,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          {path.split('/').slice(-1)[0]}
        </span>
        <LibraryChip library={item.library} />
        <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--warn-500, #f59e0b)' }}>
          default {def} → should be {native} (a{ord})
        </span>
        <button onClick={() => onOpen(detail)} className="btn ghost sm"
          title="Preview the audio (listen only). To fix this row, use “Make default” →"
          aria-label={`Preview audio for ${item.title}`}
          style={{ padding: '0 6px', fontSize: 'var(--text-2xs)' }}>🎧</button>
        <button onClick={() => onDismiss(item)} disabled={busy} className="btn ghost sm"
          title="Keep the current default track (don't ask again for this file)"
          style={{ padding: '0 8px', fontSize: 'var(--text-2xs)' }}>Dismiss</button>
        <button onClick={() => onSwap(item)} disabled={busy} className="btn primary sm"
          title={why}
          style={{ padding: '0 10px', fontSize: 'var(--text-2xs)' }}>
          {busy ? '…' : `⇄ Make ${native} default`}
        </button>
      </div>
      <div style={{
        fontSize: 'var(--text-2xs)', color: 'var(--fg-3)',
        paddingLeft: 30, lineHeight: 1.45,
      }}>
        {explainer}
      </div>
    </div>
  );
}

function EpisodeRow({ item, selected, onToggle, onOpen, busy, onSwap, onDismiss }) {
  if (item.flag === 'track_mismatch') {
    return <TrackMismatchRow item={item} selected={selected} onToggle={onToggle}
                             busy={busy} onSwap={onSwap}
                             onDismiss={onDismiss} onOpen={onOpen} />;
  }
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
    <div role="row" data-testid="review-row" style={{
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
      <span style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
        <span className="mono" title={path} style={{
          fontSize: 'var(--text-2xs)', color: 'var(--fg-3)',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0,
        }}>
          {path.split('/').slice(-1)[0]}
        </span>
        <LibraryChip library={item.library} />
      </span>
      <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-2)' }}>
        audio: {audio}
      </span>
      <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-2)' }}>
        {item.original_language || '—'}
      </span>
      <button onClick={() => onOpen(detail)}
        data-testid="review-listen"
        aria-label={`Listen to ${item.title} ${item.episode_number}`}
        title="Listen + verify individually"
        className="btn ghost sm"
        style={{ padding: '0 6px', fontSize: 'var(--text-2xs)' }}>🎧</button>
    </div>
  );
}

function SeriesGroup({ series, expanded, onToggleExpand, selected, onToggleSelectAll,
                      epSelection, onToggleEp, onOpenEp, busyPath, onSwap, onDismiss, onIgnore }) {
  const eps = series.items;
  // #310: one unified selection. Track-mismatch rows are selectable too — the
  // bulk bar partitions the selection by flag and offers Swap/Dismiss for the
  // track-mismatch half, language-assign for the rest.
  const epIds = eps.map((e) => e.file_canonical_path || e.canonical_path);
  const checkedCount = epIds.filter((id) => epSelection.has(id)).length;
  const allChecked = epIds.length > 0 && checkedCount === epIds.length;
  const indeterminate = checkedCount > 0 && !allChecked;
  return (
    <>
      <div role="row"
        onClick={onToggleExpand}
        style={{
          display: 'grid',
          gridTemplateColumns: '14px 14px 1fr 70px 70px auto',
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
        {/* #316: ignore the whole title — stop flagging missing subs for it. */}
        <button className="btn ghost sm"
          onClick={(e) => { e.stopPropagation(); onIgnore(series); }}
          title={`Ignore "${series.title}" — stop flagging missing subs for this ${series.media_type === 'movie' ? 'movie' : 'show'} (reversible)`}
          style={{ padding: '0 8px', fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>
          Ignore
        </button>
      </div>
      {expanded && eps.map((it) => {
        const id = it.file_canonical_path || it.canonical_path;
        return (
          <EpisodeRow key={id}
                      item={it}
                      selected={epSelection.has(id)}
                      onToggle={() => onToggleEp(id)}
                      onOpen={onOpenEp}
                      busy={busyPath === id}
                      onSwap={onSwap}
                      onDismiss={onDismiss} />
        );
      })}
    </>
  );
}

// #357: build the verification POST body from the bulk selection. Exported for
// tests. 2+ languages -> a multilingual verdict (lang_class='multi' + the set);
// a single pick stays single (zxx is just a single code). The caller spreads in
// confidence/evidence.
export function buildVerifyBody(canonicalPath, langs) {
  const codes = (langs || []).filter(Boolean);
  if (codes.length === 0) return null;  // empty selection -> caller must skip
  if (codes.length >= 2) {
    return {
      canonical_path: canonicalPath, lang_code: codes[0], source: 'user',
      lang_class: 'multi', lang_codes: codes,
    };
  }
  return { canonical_path: canonicalPath, lang_code: codes[0], source: 'user', lang_class: 'single' };
}

// #357: the glance lane surfaces auto-classified multilingual rows for a bulk
// eyeball. Keys on the pending-review payload's source (store 'auto-high-conf-
// multi') or the coverage display state ('multilingual').
export function isAutoMultilingualRow(r) {
  return r.audio_source === 'auto-high-conf-multi' || r.audio_source === 'multilingual';
}

// #406: order the pending lane so auto-multilingual rows sink to the bottom —
// they are already applied (not blocking), just up for an eyeball. Stable for
// every other flag. Pure + non-mutating (returns a new array) so it is testable
// and safe to call in render.
export function sortPendingRows(rows) {
  return (rows || [])
    .map((r, i) => [r, i])
    .sort((a, b) => {
      const am = a[0].flag === 'multilingual' ? 1 : 0;
      const bm = b[0].flag === 'multilingual' ? 1 : 0;
      if (am !== bm) return am - bm;
      return a[1] - b[1];  // stable
    })
    .map(([r]) => r);
}

// #406: "Accept (keep as detected)" — re-submit a multilingual row's OWN set as
// a user verdict (source='user', lang_class='multi'). Distinct from the uniform
// bulk-assign: each row carries its own lang_codes. A row missing lang_codes
// (shouldn't happen) -> null so the caller skips it.
export function acceptMultilingualBody(row) {
  const codes = (row.lang_codes || []).filter(Boolean);
  if (codes.length === 0) return null;
  return {
    canonical_path: row.file_canonical_path || row.canonical_path,
    lang_code: codes[0], source: 'user', lang_class: 'multi', lang_codes: codes,
  };
}

export function ReviewPage() {
  const langPicks = useLanguagePicks();  // #358: full Whisper set, 2-letter
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
  const [bulkLang, setBulkLang] = useState('fr');
  const [bulkRunning, setBulkRunning] = useState(false);
  const [bulkProgress, setBulkProgress] = useState({ done: 0, total: 0, errors: 0 });
  // #226: also declare a durable series/movie language rule so FUTURE
  // downloads (new episodes, re-grabbed movies) inherit the language.
  const [rememberFuture, setRememberFuture] = useState(true);
  // #406: multilingual bulk mode. When on, the single <select> becomes a
  // checkable language list and applyBulk submits the full set as a
  // lang_class='multi' verdict. Series-level multilingual intent is out of
  // scope (#357 non-goal), so "Remember for future" is disabled while on.
  const [multilingualMode, setMultilingualMode] = useState(false);
  const [bulkLangs, setBulkLangs] = useState([]);

  const toggleBulkLang = useCallback((code) => {
    setBulkLangs((prev) =>
      prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code]
    );
  }, []);

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

  // #159: per-file track-mismatch actions (swap / dismiss). busyPath disables
  // the row's buttons while a request is in flight.
  const [busyPath, setBusyPath] = useState(null);

  const swapTrack = useCallback(async (item) => {
    const path = item.file_canonical_path || item.canonical_path;
    const native = (item.mismatch_native_track_lang || item.original_language || 'original').toUpperCase();
    const def = (item.mismatch_default_track_lang || '?').toUpperCase();
    if (!window.confirm(
      `Make the ${native} audio track the default for this file?\n\n`
      + `The default is currently ${def}. subarr will flip the default-track flag in place `
      + `(instant, lossless, reversible — no re-encode). subgen will then transcribe the `
      + `original ${native} audio instead of the ${def} dub, and Plex/players will default to it too.`
    )) return;
    setBusyPath(path);
    try {
      const r = await fetch('/api/audio-lang/track-mismatch-swap', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ file_canonical_path: path }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${r.status}`);
      }
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('track swap failed', e);
      window.alert(`Track swap failed: ${e.message || e}`);
    } finally {
      setBusyPath(null);
      fetchPending({ silent: true });
    }
  }, [fetchPending]);

  const dismissTrack = useCallback(async (item) => {
    const path = item.file_canonical_path || item.canonical_path;
    setBusyPath(path);
    try {
      await fetch('/api/audio-lang/track-mismatch-dismiss', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ file_canonical_path: path }),
      });
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('track-mismatch dismiss failed', e);
    } finally {
      setBusyPath(null);
      fetchPending({ silent: true });
    }
  }, [fetchPending]);

  // #170: dismiss many track-mismatch prompts at once. Bulk language-apply
  // deliberately excludes track-mismatch rows (they need swap/dismiss, not a
  // language), so this is how a backlog of them gets cleared without per-row
  // clicks — the exact pain that prompted #170.
  const dismissTrackBulk = useCallback(async (items) => {
    const paths = items.map((it) => it.file_canonical_path || it.canonical_path).filter(Boolean);
    if (!paths.length) return;
    if (!window.confirm(
      `Dismiss ${paths.length} track-mismatch prompt${paths.length === 1 ? '' : 's'}? `
      + `They'll be hidden until re-enabled.`
    )) return;
    setBusyPath('__bulk__');
    try {
      await fetch('/api/audio-lang/track-mismatch-dismiss-bulk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ file_canonical_paths: paths }),
      });
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('bulk track-mismatch dismiss failed', e);
    } finally {
      setBusyPath(null);
      fetchPending({ silent: true });
    }
  }, [fetchPending]);

  // #310: swap many flagged track-mismatches at once. The per-file swap is heavy
  // (probe → mkvpropedit → re-probe), so the server loops it and does ONE
  // coverage refresh at the end — we fire a single POST, not N.
  const swapTrackBulk = useCallback(async (items) => {
    const paths = items.map((it) => it.file_canonical_path || it.canonical_path).filter(Boolean);
    if (!paths.length) return;
    if (!window.confirm(
      `Swap the default audio track to the original language for ${paths.length} file${paths.length === 1 ? '' : 's'}?\n\n`
      + `In-place, lossless, reversible (no re-encode). subgen will then transcribe the original `
      + `audio instead of the dub. Files already correct are skipped.`
    )) return;
    setBusyPath('__bulk__');
    try {
      const r = await fetch('/api/audio-lang/track-mismatch-swap-bulk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ file_canonical_paths: paths }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
      if (body.failed && body.failed.length) {
        window.alert(`Swapped ${body.swapped}. ${body.failed.length} could not be swapped (already correct, non-MKV, or unflagged).`);
      }
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('bulk track swap failed', e);
      window.alert(`Bulk track swap failed: ${e.message || e}`);
    } finally {
      setBusyPath(null);
      clearSelection();
      fetchPending({ silent: true });
    }
  }, [fetchPending, clearSelection]);

  // #316: per-title ignore ("I don't want subs here"). The ignored list is a
  // managed set; ignoring suppresses gap flagging + auto-queue for that title.
  const [ignored, setIgnored] = useState([]);
  const fetchIgnored = useCallback(async () => {
    try {
      const r = await fetch('/api/coverage/ignored', { credentials: 'same-origin' });
      if (r.ok) setIgnored((await r.json()).ignored || []);
    } catch { /* non-fatal */ }
  }, []);
  useEffect(() => { fetchIgnored(); }, [fetchIgnored]);

  const ignoreTitle = useCallback(async (series) => {
    const items = series.items || [];
    let path;
    if (series.media_type === 'movie') {
      path = items[0] && (items[0].file_canonical_path || items[0].canonical_path);
    } else {
      const paths = items.map((it) => it.file_canonical_path || it.canonical_path).filter(Boolean);
      path = distinctSeriesPrefixes(paths, data?.items || [])[0];
    }
    if (!path) return;
    if (!window.confirm(
      `Ignore "${series.title}"?\n\nsubarr will stop flagging missing subtitles for it `
      + `(here and on the Library page) and stop auto-queuing them. Subtitles that already `
      + `exist are left alone. You can un-ignore it any time from the Ignored list.`
    )) return;
    try {
      const r = await fetch('/api/coverage/ignore-title', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ path, note: series.title }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('ignore title failed', e);
      window.alert(`Ignore failed: ${e.message || e}`);
      return;
    }
    fetchIgnored();
    fetchPending({ silent: true });
  }, [data, fetchIgnored, fetchPending]);

  const unignoreTitle = useCallback(async (path) => {
    try {
      await fetch('/api/coverage/ignore-title?path=' + encodeURIComponent(path), {
        method: 'DELETE', credentials: 'same-origin',
      });
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('un-ignore failed', e);
    }
    fetchIgnored();
    fetchPending({ silent: true });
  }, [fetchIgnored, fetchPending]);

  // Filter + group by series.
  const { groups, tvGroups, movieGroups, totalCounts } = useMemo(() => {
    const allItems = data?.items || [];
    const counts = { all: allItems.length, suspect: 0, unknown: 0, track_mismatch: 0, multilingual: 0 };
    for (const it of allItems) {
      if (it.flag === 'suspect') counts.suspect += 1;
      else if (it.flag === 'unknown') counts.unknown += 1;
      else if (it.flag === 'track_mismatch') counts.track_mismatch += 1;
      else if (it.flag === 'multilingual') counts.multilingual += 1;  // #406
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
          media_type: it.media_type,
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
      // #406: within a group, auto-multilingual rows render last (low priority).
      g.items = sortPendingRows(g.items);
    }
    const groups = Array.from(byTitle.values()).sort((a, b) =>
      a.title.localeCompare(b.title)
    );
    // Split into TV Shows vs Movies (mirrors Coverage). media_type 'movie' →
    // Movies; everything else (episode / show / unknown) → TV Shows.
    const tvGroups = groups.filter((g) => g.media_type !== 'movie');
    const movieGroups = groups.filter((g) => g.media_type === 'movie');
    return { groups, tvGroups, movieGroups, totalCounts: counts };
  }, [data, filter, search]);

  // #310: track-mismatch rows are now selectable alongside language-assignable
  // ones, so split the current selection by flag — each half gets its own bulk
  // action (you can't assign a language to a track-mismatch, or swap a track on
  // a plain language row). Looked up against data.items, not the filtered view.
  const { selTmItems, selAssignPaths, selMultiRows } = useMemo(() => {
    const items = data?.items || [];
    const sel = items.filter((it) => epSelection.has(it.file_canonical_path || it.canonical_path));
    return {
      selTmItems: sel.filter((it) => it.flag === 'track_mismatch'),
      selAssignPaths: sel
        .filter((it) => it.flag !== 'track_mismatch')
        .map((it) => it.file_canonical_path || it.canonical_path),
      // #406: multilingual rows selected for "Accept (keep as detected)".
      selMultiRows: sel.filter((it) => it.flag === 'multilingual'),
    };
  }, [data, epSelection]);

  // Bulk action — fires one POST per file. Each one runs the full
  // verification pipeline (Sonarr propagation + Bazarr sync trigger), so
  // bulk-assigning Spanish to a whole telenovela closes the bazarr-blind
  // loop for every episode. Serial-ish; we cap concurrency at 4 so the
  // user gets fast feedback but we don't slam Sonarr.
  const applyBulk = useCallback(async () => {
    // #310: only the language-assignable rows — track-mismatch rows in the same
    // selection are handled by Swap all / Dismiss all instead.
    const paths = selAssignPaths;
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
          // #406: multilingual mode submits the full checked set; otherwise the
          // single bulkLang. Empty selection -> builder returns null -> skip.
          const verifyBody = buildVerifyBody(p, multilingualMode ? bulkLangs : [bulkLang]);
          if (!verifyBody) { done += 1; setBulkProgress({ done, total: paths.length, errors }); continue; }
          const r = await fetch('/api/audio-lang/verifications', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ ...verifyBody, confidence: 1.0, evidence: { bulk: true } }),
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
    // #226: if requested, declare one durable intent rule per distinct
    // series/movie in the selection. Best-effort — failures here never fail
    // the per-file bulk above (the primary action); they bump the error count.
    if (rememberFuture && !multilingualMode) {
      const prefixes = distinctSeriesPrefixes(paths, data?.items || []);
      for (const prefix of prefixes) {
        try {
          const r = await fetch('/api/audio-lang/series-intent', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ series_prefix: prefix, lang_code: bulkLang }),
          });
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
        } catch (e) {
          // eslint-disable-next-line no-console
          console.error('series-intent declare failed for', prefix, e);
          errors += 1;
          setBulkProgress({ done, total: paths.length, errors });
        }
      }
    }
    setBulkRunning(false);
    clearSelection();
    // Refetch in case some verifies failed; ensures the list is honest.
    fetchPending({ silent: true });
  }, [selAssignPaths, bulkLang, multilingualMode, bulkLangs, fetchPending, clearSelection, rememberFuture, data]);

  // #406: "Accept (keep as detected)" — confirm each selected multilingual row's
  // OWN detected set as a user verdict (source='user'). Per-row (each carries its
  // own lang_codes), distinct from the uniform bulk-assign. Reuses the 4-worker
  // progress pattern; rows missing lang_codes are skipped (builder returns null).
  const acceptSelected = useCallback(async () => {
    const rows = selMultiRows;
    if (!rows.length) return;
    setBulkRunning(true);
    setBulkProgress({ done: 0, total: rows.length, errors: 0 });
    let done = 0; let errors = 0;
    const queue = rows.slice();
    async function worker() {
      while (queue.length) {
        const row = queue.shift();
        const body = acceptMultilingualBody(row);
        const p = row.file_canonical_path || row.canonical_path;
        if (!body) { done += 1; setBulkProgress({ done, total: rows.length, errors }); continue; }
        try {
          const r = await fetch('/api/audio-lang/verifications', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ ...body, confidence: 1.0, evidence: { accept_multi: true } }),
          });
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          window.dispatchEvent(new CustomEvent('audio-lang-verified', {
            detail: { file_canonical_path: p, lang_code: body.lang_code },
          }));
        } catch (e) {
          // eslint-disable-next-line no-console
          console.error('accept multilingual failed for', p, e);
          errors += 1;
        }
        done += 1;
        setBulkProgress({ done, total: rows.length, errors });
      }
    }
    await Promise.all([worker(), worker(), worker(), worker()]);
    setBulkRunning(false);
    clearSelection();
    fetchPending({ silent: true });
  }, [selMultiRows, fetchPending, clearSelection]);

  const filterPills = [
    { id: 'all',     label: `all (${totalCounts.all})` },
    { id: 'suspect', label: `suspect (${totalCounts.suspect})` },
    { id: 'unknown', label: `unknown (${totalCounts.unknown})` },
    { id: 'track_mismatch', label: `track mismatch (${totalCounts.track_mismatch})` },
    { id: 'multilingual', label: `multilingual (${totalCounts.multilingual})` },  // #406
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
            Files where subarr wants you to confirm the audio language, plus files whose default
            audio track isn't the show's original language (⇄ track mismatch — one-click swap to
            stop double-translated subs). Pick a series, tick whole shows for bulk assignment,
            or click 🎧 to listen first.
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

      {/* #170: track-mismatch rows can't be cleared by the bulk language-apply
          (which excludes them). Surface a bulk dismiss + explain the split. */}
      {filter === 'track_mismatch' && groups.length > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '4px 2px 10px' }}>
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>
            These need a track swap (fix the default track) or a dismiss — bulk language-apply doesn't touch them.
          </span>
          <span style={{ flex: 1 }} />
          <button className="btn" disabled={busyPath === '__bulk__'}
            onClick={() => dismissTrackBulk(groups.flatMap((g) => g.items))}>
            {busyPath === '__bulk__'
              ? 'Dismissing…'
              : `Dismiss all visible (${groups.reduce((s, g) => s + g.items.length, 0)})`}
          </button>
        </div>
      )}

      {/* #316: managed list of ignored titles — un-ignore brings them back. */}
      {ignored.length > 0 && (
        <details style={{
          background: 'var(--bg-2)', border: 'var(--border)',
          borderRadius: 'var(--radius-md)', padding: '6px 12px',
        }}>
          <summary style={{ cursor: 'pointer', fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
            Ignored titles ({ignored.length}) — subarr isn't flagging missing subs for these
          </summary>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 8 }}>
            {ignored.map((row) => {
              const seg = row.path.replace(/\/$/, '').split('/').slice(-1)[0];
              return (
                <div key={row.path} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span className="mono" title={row.path} style={{
                    flex: 1, minWidth: 0, fontSize: 'var(--text-2xs)', color: 'var(--fg-2)',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>
                    {row.note || seg}
                    {row.path.endsWith('/') && <span style={{ color: 'var(--fg-3)' }}> (series)</span>}
                  </span>
                  <button className="btn ghost sm"
                    onClick={() => unignoreTitle(row.path)}
                    style={{ padding: '0 8px', fontSize: 'var(--text-2xs)' }}>
                    Un-ignore
                  </button>
                </div>
              );
            })}
          </div>
        </details>
      )}

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
          {[
            { label: 'TV Shows', noun: 'series', plural: 'series', list: tvGroups },
            { label: 'Movies', noun: 'movie', plural: 'movies', list: movieGroups },
          ].map((section) =>
            section.list.length === 0 ? null : (
              <div key={section.label}>
                {/* Category header — mirrors Coverage's TV Shows / Movies split. */}
                <div style={{
                  display: 'flex', alignItems: 'baseline', gap: 8,
                  margin: '14px 2px 4px', fontSize: 'var(--text-xs)', fontWeight: 700,
                  letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--fg-2)',
                }}>
                  {section.label}
                  <span style={{ color: 'var(--fg-3)', fontWeight: 600, textTransform: 'none', letterSpacing: 0 }}>
                    {section.list.length} {section.list.length === 1 ? section.noun : section.plural}
                  </span>
                </div>
                {section.list.map((g) => (
                  <SeriesGroup
                    key={g.title}
                    series={g}
                    expanded={expandedSeries.has(g.title)}
                    onToggleExpand={() => toggleExpand(g.title)}
                    epSelection={epSelection}
                    onToggleSelectAll={toggleSelectAll}
                    onToggleEp={toggleEp}
                    onOpenEp={openReview}
                    busyPath={busyPath}
                    onSwap={swapTrack}
                    onDismiss={dismissTrack}
                    onIgnore={ignoreTitle}
                  />
                ))}
              </div>
            )
          )}
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

          {/* Language-assignable rows (suspect / unknown). */}
          {selAssignPaths.length > 0 && (
            <>
              <span style={{ color: 'var(--bg-5)' }}>·</span>
              <label style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
                Assign audio language
              </label>
              {multilingualMode ? (
                <div role="group" aria-label="Multilingual: select languages"
                     style={{
                       display: 'flex', flexWrap: 'wrap', gap: 6, maxWidth: 420,
                       maxHeight: 84, overflowY: 'auto', padding: '4px 6px',
                       background: 'var(--bg-1)', border: 'var(--border)',
                       borderRadius: 'var(--radius-md)',
                     }}>
                  {langPicks.map(([code, name]) => (
                    <label key={code}
                      style={{
                        display: 'inline-flex', alignItems: 'center', gap: 4,
                        fontSize: 'var(--text-2xs)', color: 'var(--fg-1)', cursor: 'pointer',
                      }}>
                      <input type="checkbox"
                        checked={bulkLangs.includes(code)}
                        onChange={() => toggleBulkLang(code)}
                        disabled={bulkRunning}
                        style={{ accentColor: 'var(--violet-500)' }} />
                      {name} ({code})
                    </label>
                  ))}
                </div>
              ) : (
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
                  {langPicks.map(([code, name]) => (
                    <option key={code} value={code}>{name} ({code})</option>
                  ))}
                </select>
              )}
              <label style={{
                display: 'inline-flex', alignItems: 'center', gap: 6,
                fontSize: 'var(--text-xs)', color: 'var(--fg-1)', cursor: 'pointer',
              }}
                title="Mark these files as multilingual (multiple audio languages in one file).">
                <input type="checkbox" checked={multilingualMode}
                  onChange={(e) => setMultilingualMode(e.target.checked)}
                  disabled={bulkRunning}
                  style={{ accentColor: 'var(--violet-500)' }} />
                Multilingual
              </label>
              <label style={{
                display: 'inline-flex', alignItems: 'center', gap: 6,
                fontSize: 'var(--text-xs)', color: 'var(--fg-1)',
                cursor: multilingualMode ? 'not-allowed' : 'pointer',
                opacity: multilingualMode ? 0.5 : 1,
              }}
                title="Also save a rule so new episodes — and re-downloaded movies — of these titles inherit this language automatically. A per-file correction always overrides it.">
                <input type="checkbox" checked={rememberFuture && !multilingualMode}
                  onChange={(e) => setRememberFuture(e.target.checked)}
                  disabled={bulkRunning || multilingualMode}
                  style={{ accentColor: 'var(--violet-500)' }} />
                Remember for future downloads
              </label>
              {/* #317 Slice C: rules are declared here, managed in Settings — close the loop. */}
              <a
                href="/settings#lang-rules"
                title="View, edit, or revoke saved language rules."
                style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)', whiteSpace: 'nowrap' }}
              >
                manage rules →
              </a>
              <button className="btn primary" onClick={applyBulk} disabled={bulkRunning}>
                {bulkRunning ? 'Applying…' : `Apply to ${selAssignPaths.length}`}
              </button>
            </>
          )}

          {/* #310: track-mismatch rows in the selection — swap or dismiss in bulk
              instead of assigning a language. */}
          {selTmItems.length > 0 && (
            <>
              <span style={{ color: 'var(--bg-5)' }}>·</span>
              <span style={{ fontSize: 'var(--text-xs)', color: 'var(--warn-500, #f59e0b)' }}>
                {selTmItems.length} track mismatch
              </span>
              <button className="btn primary" disabled={busyPath === '__bulk__'}
                title="Make the original-language audio the default track for each (in-place, lossless, reversible)"
                onClick={() => swapTrackBulk(selTmItems)}>
                {busyPath === '__bulk__' ? 'Swapping…' : `⇄ Swap all (${selTmItems.length})`}
              </button>
              <button className="btn" disabled={busyPath === '__bulk__'}
                title="Keep the current default track for each (don't ask again)"
                onClick={() => dismissTrackBulk(selTmItems)}>
                Dismiss all ({selTmItems.length})
              </button>
            </>
          )}

          {/* #406: selected multilingual rows — confirm each row's own detected
              set as a user verdict so the lane empties as they are reviewed. */}
          {selMultiRows.length > 0 && (
            <>
              <span style={{ color: 'var(--bg-5)' }}>·</span>
              <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
                🌐 {selMultiRows.length} multilingual
              </span>
              <button className="btn primary" onClick={acceptSelected} disabled={bulkRunning}
                title="Confirm each selected file's detected language set as-is (marks them user-verified so they leave this list).">
                {bulkRunning ? 'Applying…' : `Accept (keep as detected) (${selMultiRows.length})`}
              </button>
            </>
          )}

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
        </div>
      )}

      {/* Single-file modal — reused from Coverage. Listens for the
          open-audio-review event dispatched by the 🎧 button. */}
      <AudioReviewModal />
    </main>
  );
}
