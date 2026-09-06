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

const { useState, useEffect, useCallback, useMemo, useRef } = React;

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
  // #494 P1-S4: `series` is a complete server-selected group, so eps.length IS
  // the truthful per-group matching-file count (== series.file_count). "Select
  // all" adds every rendered row's explicit path below — never an implicit
  // group-level mutation.
  const epIds = groupExplicitPaths(series);
  const checkedCount = epIds.filter((id) => epSelection.has(id)).length;
  const countUnit = series.media_type === 'movie'
    ? (eps.length === 1 ? 'file' : 'files')
    : (eps.length === 1 ? 'ep' : 'eps');
  const ariaUnit = series.media_type === 'movie'
    ? (eps.length === 1 ? 'file' : 'files')
    : (eps.length === 1 ? 'episode' : 'episodes');
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
                  label={`Select all ${eps.length} ${ariaUnit} of ${series.title}`} />
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
        <span className="mono num" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-2)' }}
          title={`${eps.length} matching file${eps.length === 1 ? '' : 's'} in this group`}>
          {eps.length} {countUnit}
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

// #457: the bulk-assign confirmation must name the codes that will ACTUALLY be
// submitted. It interpolated the single-select `bulkLang` even in multilingual
// mode, so a user picking en+ja was asked to confirm "fr" -- whatever stale
// value the single dropdown was sitting on. The submission was already correct;
// only the dialog lied, which is the worse way round, because that prompt is the
// user's last chance to stop a wrong bulk writeback to Sonarr and Bazarr.
//
// #406 fixed this same class a few lines below for the dispatched event and did
// not touch the dialog. Deriving both from one array is what prevents a third.
export function bulkAssignConfirmText(codes, fileCount) {
  const list = (codes || []).filter(Boolean);
  if (list.length === 0) return null;  // nothing selected -> caller must not prompt
  const files = `${fileCount} file${fileCount === 1 ? '' : 's'}`;
  const quoted = list.map((c) => `"${c}"`).join(', ');
  const noun = list.length === 1 ? 'audio language' : 'audio languages';
  return `Assign ${quoted} as the ${noun} for ${files}?`
    + `\n\nEach file will be saved locally, pushed to Sonarr's per-file language record, `
    + `and Bazarr will be triggered to re-sync. The selected rows will leave this list once verified.`;
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

// #494 P3-S2: the explicit per-file paths of a rendered server group. SeriesGroup's
// "Select all" and every bulk path resolve each row's `file_canonical_path ||
// canonical_path`; this is the single source so a complete rendered group maps to
// EVERY matching explicit file path — never an implicit group-level mutation. Pure
// + non-mutating, exported for the Phase-3 selection regressions. A group produced
// by arrangeServerGroups carries `.items`; this also tolerates a bare {items:[...]}.
export function groupExplicitPaths(group) {
  return (group?.items || []).map((e) => e.file_canonical_path || e.canonical_path);
}

// epSelection is page-scoped: its ids are the explicit paths of rendered rows. An
// authoritative setData() following a NON-batch refetch (manual Refresh, a foreign
// audio-lang-verified event, per-file swap/dismiss cleanup) can serve a smaller
// page, dropping previously-selected rows. Prune against the incoming page's own
// explicit row ids so selectedCount never counts vanished rows after the refetch.
// Pure + non-mutating; returns the SAME Set when nothing was removed so the
// caller's functional update short-circuits. Exported for seam tests.
export function pruneSelectionAgainstRows(visibleRows, epSelection) {
  const visible = new Set(
    (visibleRows || []).map((it) => it.file_canonical_path || it.canonical_path).filter(Boolean)
  );
  let changed = false;
  const next = new Set();
  for (const id of epSelection || []) {
    if (visible.has(id)) next.add(id); else changed = true;
  }
  return changed ? next : epSelection;
}

// #494 P2-S5: pure decision for Review's `audio-lang-verified` listener. While
// a Review-originated language/multilingual batch is in flight, `gate` is a Set
// of the batch's explicit file paths. The listener must NOT trigger Review's own
// pending-review refetch for each file in that batch — the batch issues exactly
// ONE authoritative silent refetch when it finishes. Events for files OUTSIDE
// the batch (an arena live-sweep landing mid-batch, a single-file modal verify on
// another file) still refetch so external verification is never dropped, and an
// event with no trackable identity is not suppressed (we can't prove it belongs
// to the batch). The global event itself is always dispatched — only Review's
// reaction here is gated, leaving other listeners untouched. Pure + non-mutating
// so it is unit-testable without a DOM (Phase 3). Exported for those tests.
//   gate      — null when no Review bulk batch is in flight, else a Set of paths.
//   eventPath — the verifying file's file_canonical_path (from event.detail).
// Returns true when Review should run its silent pending-review refetch.
export function shouldRefetchAfterVerify(gate, eventPath) {
  if (!gate) return true;             // normal operation: refetch per verify
  if (gate.size === 0) return false;  // armed but no identity to match -> Review batch
  return !gate.has(eventPath);        // suppress the batch's own files only
}

// P3: build the /api/audio-lang/pending-review query string for server-side
// search + page slicing. The server validates these (search max_length=200,
// limit 1..500, offset >= 0) and applies search before slicing. Exported for
// tests. Empty search is omitted so the URL stays clean.
//
// #494: Review alone opts into the additive grouped mode — the server then
// pages COMPLETE groups (limit/offset address groups, not files). Grouped mode
// is the Review default because Review is this helper's only caller; the
// non-group /pending-review consumers (coverage/chrome/dashboard) build their
// own queries and are untouched.
export function buildReviewQuery({ search = '', flag = 'all', limit = 200, offset = 0, grouped = true }) {
  const q = new URLSearchParams();
  const s = (search || '').trim();
  if (s) q.set('search', s);
  if (flag && flag !== 'all') q.set('flag', flag);
  q.set('limit', String(limit));
  q.set('offset', String(offset));
  if (grouped) q.set('grouped', 'true');
  return q.toString();
}

// P3: derive pagination facts from the server's TRUTHFUL total (`count` is the
// total matching rows, not the page length) and the current page. Exported for
// tests. hasNext/hasPrev drive the disabled boundaries of the page controls.
//
// #494: the same unit-agnostic math pages by whichever total the server pages —
// FILES by default (legacy contract, `total`/`count` are files) or GROUPS in
// grouped mode. Review passes grouped=true + groupCount so pageNumber/range/
// next/previous describe group pages, while `count` (matching files) is still
// carried through for the truthful file-total labels. Legacy callers/tests pass
// no grouped flag and keep the exact prior file behavior.
export function computePagination({ count = 0, limit = 200, offset = 0, grouped = false, groupCount = 0 }) {
  const fileCount = Math.max(0, count || 0);
  const size = Math.max(1, limit || 1);
  // The paging-unit total: groups in grouped mode, files otherwise (legacy).
  const unitTotal = grouped ? Math.max(0, groupCount || 0) : fileCount;
  const totalPages = Math.max(1, Math.ceil(unitTotal / size));
  return {
    // `total` keeps its legacy meaning (files) unless grouped — Review derives
    // its group range/next/prev from the group total and reads file totals from
    // `fileCount`/`count` below.
    total: unitTotal,
    fileCount,
    groupCount: grouped ? unitTotal : 0,
    totalPages,
    pageNumber: Math.floor(offset / size) + 1,
    shownStart: unitTotal === 0 ? 0 : offset + 1,
    shownEnd: Math.min(offset + size, unitTotal),
    hasPrev: offset > 0,
    hasNext: offset + size < unitTotal,
  };
}

// #494: arrange the server's grouped response for rendering. The backend pages
// complete groups and flattens every row of the selected groups into `items`,
// stamping each row with its group's stable `group_key`. This helper rebuilds
// the render groups from `groups[]` (the authority for which groups exist, in
// what order) by pulling each group's rows out of `items` via that link — it
// never re-derives membership from `title`, never re-applies the flag filter,
// and never drops a returned row. The only ordering we apply is presentation-
// level WITHIN a group (numeric episode order, auto-multilingual last), which
// cannot change group membership or how many rows a group shows. Pure + non-
// mutating so it is safe in render and testable in isolation. Exported for the
// Phase-3 rendering regressions.
export function arrangeServerGroups({ groups = [], items = [] }) {
  const byKey = new Map();
  for (const it of items || []) {
    const k = it?.group_key ?? '';
    if (!byKey.has(k)) byKey.set(k, []);
    byKey.get(k).push(it);
  }
  const buildGroup = (meta) => {
    // Copy so the episode sort below never mutates the shared byKey buckets.
    const rows = (byKey.get(meta?.key) || []).slice();
    rows.sort((a, b) =>
      (a.episode_number || '').localeCompare(b.episode_number || '', undefined, { numeric: true })
    );
    const ordered = sortPendingRows(rows);  // #406: auto-multilingual rows sink last
    const first = ordered[0] || {};
    return {
      // Stable backend identity — never the bare title, so duplicate display
      // titles stay distinct groups (distinct React keys and expansion state).
      key: meta?.key,
      title: meta?.title ?? '(unknown)',
      media_type: meta?.media_type,
      library: meta?.library,
      canonical_root: meta?.canonical_root,
      // Truthful per-group matching count == rows actually rendered below.
      file_count: ordered.length,
      original_language: first.original_language,
      items: ordered,
    };
  };
  const arranged = (groups || []).map(buildGroup);
  const tvGroups = arranged.filter((g) => g.media_type !== 'movie');
  const movieGroups = arranged.filter((g) => g.media_type === 'movie');
  const pageFileCount = arranged.reduce((sum, g) => sum + g.items.length, 0);
  return { groups: arranged, tvGroups, movieGroups, pageFileCount };
}

// #494 P2-S5/P3-S3: the shared per-file verification batch driver used by both
// Review bulk actions — language-assign (`applyBulk`) and multilingual accept
// (`acceptSelected`). It encapsulates the bulk-event contract so it can be unit
// tested without a DOM:
//   - arms the bulk-in-flight gate (setGate) with the batch's explicit paths
//     BEFORE the first file is processed, so the `audio-lang-verified` listener
//     (shouldRefetchAfterVerify) suppresses Review's per-file refetch for the
//     whole batch;
//   - one `emitVerified(path, body)` per SUCCESSFUL file (never on a failed or a
//     skipped null-body file) — the global event still reaches arena/other
//     consumers exactly as before;
//   - clears the gate in a finally (success AND error cleanup) via clearGate,
//     then calls `refetchAfterBatch` exactly once — the single authoritative
//     silent pending-review refetch for the batch.
// The per-file mutation transport (the fetch + evidence) lives inside the
// caller's injected `submit` — this helper never restructures it — and
// `afterBatch` (remember-for-future in applyBulk) still runs inside the
// gate-guarded try, before the gate clears. Pure of DOM/React and exported for
// the Phase-3 bulk-event regressions; the component only wires real fetches,
// the window dispatch, the gate ref, and fetchPending in here.
export async function runVerifyBatch({
  items,               // queue of batch units (explicit paths for applyBulk, rows for acceptSelected)
  total,               // progress denominator (paths.length / rows.length)
  submit,              // async (unit) => verifyBody|null  — build body + POST; null => skip; throw => failure
  pathOf,              // (unit) => explicit path          — gate membership + event detail identity
  emitVerified,        // (path, verifyBody) => void       — one global event per successful file
  onProgress,          // (done, total, errors) => void
  setGate,             // (Set) => void                    — arm bulkGateRef.current
  clearGate,           // () => void                       — clear in success AND error cleanup
  finish,              // () => void                       — post-batch UI state (bulkRunning off, clear selection)
  afterBatch,          // async (ctx) => void              — optional gate-guarded post-worker work (remember-for-future)
  refetchAfterBatch,   // () => void                       — THE one authoritative refetch, after gate clear
  concurrency = 4,     // cap like the original 4-worker loops
}) {
  const units = (items || []).slice();
  const stats = { done: 0, errors: 0 };
  const bump = () => onProgress && onProgress(stats.done, total, stats.errors);
  setGate(new Set(units.map((u) => pathOf(u)).filter(Boolean)));
  async function worker() {
    while (units.length) {
      const unit = units.shift();
      let body;
      try {
        body = await submit(unit);
      } catch (e) {
        // eslint-disable-next-line no-console
        console.error('bulk verify failed for', pathOf(unit), e);
        stats.errors += 1; stats.done += 1; bump();
        continue;
      }
      if (!body) { stats.done += 1; bump(); continue; }  // empty builder -> skip, no fetch, no emit
      emitVerified(pathOf(unit), body);                   // exactly one global event per success
      stats.done += 1; bump();
    }
  }
  try {
    await Promise.all(Array.from({ length: Math.max(1, concurrency) }, () => worker()));
    if (afterBatch) await afterBatch(stats);
  } finally {
    clearGate();
    if (finish) finish();
  }
  refetchAfterBatch();
  return { done: stats.done, errors: stats.errors };
}


// [#453] Renamed or deleted files leave rows keyed on the OLD canonical path.
// Nothing in the coverage path checks whether a file still exists, so re-walking
// never clears them and users have resorted to hand-written SQL.
//
// This auto-detects on load but NEVER deletes on its own. The apply step
// destroys hand-confirmed audio-language verifications, which cost hours to
// rebuild, so the user sees the exact list first and clicks to confirm.
function OrphanBanner() {
  const [state, setState] = useState(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [done, setDone] = useState(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await fetch('/api/admin/db/orphans', { credentials: 'same-origin' });
        if (!r.ok) return;                 // silent: this is a nicety, not core UI
        const body = await r.json();
        if (alive) setState(body);
      } catch { /* offline or blocked: stay silent rather than alarm */ }
    })();
    return () => { alive = false; };
  }, []);

  const apply = async () => {
    setBusy(true);
    try {
      const r = await fetch('/api/admin/db/orphans/prune', {
        method: 'POST', credentials: 'same-origin',
      });
      const body = await r.json();
      setDone(body);
      // Re-fetching the list is what proves the rows are gone; trusting the
      // POST's own echo would be believing the thing under test.
      const again = await fetch('/api/admin/db/orphans', { credentials: 'same-origin' });
      if (again.ok) setState(await again.json());
    } catch (e) {
      setDone({ safe: false, reason: `Prune failed: ${e.message || e}` });
    } finally {
      setBusy(false);
    }
  };

  if (dismissed || !state) return null;
  const n = state.would_delete || 0;
  const blocked = n > 0 && state.safe === false;
  if (n === 0 && !done) return null;

  // A refusal is not an error. It means the storage looks unavailable, and the
  // honest thing is to explain that rather than offer a button that would
  // delete everything the user has confirmed.
  const tone = blocked ? 'var(--warn, #d98324)' : 'var(--fg-2)';

  return (
    <div style={{
      border: `1px solid ${blocked ? tone : 'var(--border, #333)'}`,
      borderRadius: 8, padding: '10px 12px', fontSize: 'var(--text-sm)',
      display: 'flex', flexDirection: 'column', gap: 8,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ color: tone }}>{blocked ? '⚠' : '●'}</span>
        <div style={{ flex: 1 }}>
          {done ? (
            <span>
              {done.safe
                ? `Removed ${done.deleted_total} orphaned row${done.deleted_total === 1 ? '' : 's'}.`
                : done.reason}
            </span>
          ) : blocked ? (
            <span><strong>{n} rows reference files that are missing, and cleanup is on hold.</strong> {state.reason}</span>
          ) : (
            <span>
              <strong>{n} row{n === 1 ? '' : 's'} reference files that no longer exist.</strong>{' '}
              Usually renames. Cleaning them up clears stale entries from Review.
            </span>
          )}
        </div>
        {!done && !blocked && (
          <button type="button" onClick={() => setOpen(v => !v)} style={{ whiteSpace: 'nowrap' }}>
            {open ? 'Hide' : 'Show these'}
          </button>
        )}
        <button type="button" onClick={() => setDismissed(true)} aria-label="Dismiss">
          {'×'}
        </button>
      </div>

      {open && !done && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{
            maxHeight: 180, overflowY: 'auto', fontFamily: 'var(--mono, monospace)',
            fontSize: 'var(--text-xs)', color: 'var(--fg-2)',
            border: '1px solid var(--border, #333)', borderRadius: 6, padding: 8,
          }}>
            {state.missing.map(pth => <div key={pth}>{pth}</div>)}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <button type="button" onClick={apply} disabled={busy}>
              {busy ? 'Removing...' : `Remove ${n} row${n === 1 ? '' : 's'}`}
            </button>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
              This deletes saved audio-language verifications for these files. It cannot be undone.
            </span>
          </div>
        </div>
      )}
    </div>
  );
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
  // P3: server-side search + pagination. `search` is the live input value;
  // `debouncedSearch` is the term that actually drives the request (see the
  // debounce effect below). `limit` is the page size and `offset` the current
  // page's starting row into the server's searched set.
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [limit, setLimit] = useState(200);
  const [offset, setOffset] = useState(0);
  // P3-S4: a request sequence token so a stale response (from an older query or
  // page) can never overwrite a newer one — the fetch guard in fetchPending.
  const fetchSeq = useRef(0);
  // #494 P2-S5: bulk-in-flight gate. Non-null (a Set of the batch's explicit file
  // paths) while a Review-originated applyBulk/acceptSelected batch is running.
  // The `audio-lang-verified` listener reads it to skip Review's own per-file
  // pending-review refetch during the batch; the batch performs one authoritative
  // refetch when it finishes. A ref (not state) so arming/clearing never triggers
  // a re-render or re-subscribes the event listener.
  const bulkGateRef = useRef(null);
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

  // P3: debounce the server search so typing doesn't fire a request per
  // keystroke. When the term settles, reset to page 1 (a new search starts at
  // the top of the results).
  useEffect(() => {
    const id = setTimeout(() => {
      setDebouncedSearch(search);
      setOffset(0);
    }, 250);
    return () => clearTimeout(id);
  }, [search]);

  // P3-S3: epSelection is page-scoped (keyed on file_canonical_path||canonical_path).
  // Changing the search text changes which rows are visible, so drop any stale
  // selection the moment the query text changes (not only once it settles).
  useEffect(() => {
    setEpSelection(new Set());
  }, [search]);

  // P3-S3: a filter-pill transition changes the visible set on the same page
  // too — clear the stale page-scoped selection so the bulk bar never targets
  // rows that are no longer visible.
  useEffect(() => {
    setEpSelection(new Set());
  }, [filter]);

  // Selection is intentionally page-scoped; navigating to another page or
  // changing its size must not leave a bulk-action bar referring to rows that
  // are no longer visible.
  useEffect(() => {
    setEpSelection(new Set());
  }, [offset, limit]);

  const fetchPending = useCallback(async ({ silent = false } = {}) => {
    // First-paint only sets `loading`; every subsequent fetch (silent or
    // user-initiated Refresh click) sets `isRefetching` so the existing
    // list stays visible with a small spinner — no blank-and-redraw.
    // P3-S4: claim a fresh sequence token; a stale response (an older query or
    // page) that resolves later must not clobber the newer one.
    const seq = ++fetchSeq.current;
    setIsRefetching(true);
    const startedAt = Date.now();
    try {
      const q = buildReviewQuery({ search: debouncedSearch, flag: filter, limit, offset, grouped: true });
      const r = await fetch(`/api/audio-lang/pending-review?${q}`, {
        credentials: 'same-origin',
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const payload = await r.json();
      const items = payload?.items || [];
      const groups = payload?.groups;                     // present in grouped mode
      const isGrouped = Array.isArray(groups);
      if (seq !== fetchSeq.current) return;  // a newer query/page won the race
      // P3-S2 / #494 empty-last-page recovery — if a page became empty between
      // fetches (e.g. its whole group was resolved/ignored), step back to the
      // previous valid page and refetch instead of leaving a blank invalid page.
      // In grouped mode an "empty page" means no GROUPS landed on it and the
      // page total is measured in groups; `offset` here is a group offset too.
      // Guarded by the seq check above so a stale empty response can't step the
      // offset back while a newer fetch is in flight. The offset change below
      // re-triggers fetchPending via the query effect.
      const pageEmpty = isGrouped ? groups.length === 0 : items.length === 0;
      const pageTotal = isGrouped ? (payload?.group_count || 0) : (payload?.count || 0);
      if (pageEmpty && pageTotal > 0 && offset > 0) {
        setOffset(Math.max(0, offset - limit));
        return;
      }
      setData(payload);
      // #494 P2-S6: prune the page-scoped selection to rows still present on this
      // authoritative page. A non-batch refetch (manual Refresh, a foreign
      // audio-lang-verified event, per-file swap/dismiss cleanup) removes rows;
      // keeping their ids in epSelection would inflate "N files selected" with
      // rows that are no longer shown. No-op when nothing disappeared.
      setEpSelection((prev) => (prev.size ? pruneSelectionAgainstRows(items, prev) : prev));
      setError(null);
      setLastRefreshedAt(Date.now());
    } catch (e) {
      // On refetch error, KEEP the stale data visible. The error banner
      // surfaces below the list. Only blank on first-paint failure.
      if (seq === fetchSeq.current) setError(e);
    } finally {
      // Minimum 350ms display so a sub-100ms fetch is still perceptible.
      // Below ~300ms the human eye registers the spinner as a flicker,
      // not a state change — they think the button didn't fire.
      const elapsed = Date.now() - startedAt;
      const padding = Math.max(0, 350 - elapsed);
      if (padding > 0) await new Promise(resolve => setTimeout(resolve, padding));
      if (seq === fetchSeq.current) {
        setIsRefetching(false);
        setLoading(false);
      }
    }
  }, [debouncedSearch, filter, limit, offset]);

  useEffect(() => {
    fetchPending();
    // #494 P2-S5: Review's own per-file language/multilingual batches dispatch
    // `audio-lang-verified` once per successful file. While such a batch is in
    // flight (bulkGateRef.current holds the batch's explicit paths), skip Review's
    // own pending-review refetch for each batch file — the batch issues exactly
    // ONE authoritative silent refetch when it finishes (see applyBulk/
    // acceptSelected). Events for files OUTSIDE the batch still refetch. The
    // global event is always dispatched to every listener; only Review's reaction
    // here is gated, so arena/other consumers are unaffected.
    const onVerified = (e) => {
      const eventPath = e && e.detail && e.detail.file_canonical_path;
      if (shouldRefetchAfterVerify(bulkGateRef.current, eventPath)) {
        fetchPending({ silent: true });
      }
    };
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
    // P2-S6: single bulk flow guard — block while a language/multilingual batch
    // is running or any per-row/other-bulk track op is in flight.
    if (busyPath || bulkRunning) return;
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
      // #494 P2-S6: dismissing removes these rows from the queue — drop their
      // explicit paths from the selection so a stale "N files selected" count
      // can't linger after the refetch (mirrors swapTrackBulk's clear below).
      clearSelection();
      fetchPending({ silent: true });
    }
  }, [fetchPending, clearSelection, busyPath, bulkRunning]);

  // #310: swap many flagged track-mismatches at once. The per-file swap is heavy
  // (probe → mkvpropedit → re-probe), so the server loops it and does ONE
  // coverage refresh at the end — we fire a single POST, not N.
  const swapTrackBulk = useCallback(async (items) => {
    const paths = items.map((it) => it.file_canonical_path || it.canonical_path).filter(Boolean);
    if (!paths.length) return;
    // P2-S6: single bulk flow guard — never interleave with a language/multilingual
    // batch (bulkRunning) or another per-row/bulk track op (busyPath).
    if (busyPath || bulkRunning) return;
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
  }, [fetchPending, clearSelection, busyPath, bulkRunning]);

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

  // #494: render the SERVER's complete, filtered, grouped result. The backend
  // applies search + the flag filter per-row BEFORE grouping and pages whole
  // groups, so `groups[]` is the authority for which groups exist and in what
  // order, and every returned group carries all of its matching rows. We never
  // re-group by title, never re-apply the flag filter, and never change group
  // membership here (see arrangeServerGroups). Only the flag-pill totals come
  // from the searched-set counts_by_flag so pills stay stable across a filter.
  const { groups, tvGroups, movieGroups, pageFileCount, totalCounts } = useMemo(() => {
    const arranged = arrangeServerGroups({ groups: data?.groups, items: data?.items });
    const countsByFlag = data?.counts_by_flag || {};
    const counts = {
      all: Object.values(countsByFlag).reduce((sum, count) => sum + count, 0) || data?.count || 0,
      suspect: 0, unknown: 0, track_mismatch: 0, multilingual: 0,
      ...countsByFlag,
    };
    return { ...arranged, totalCounts: counts };
  }, [data]);

  // #310: track-mismatch rows are now selectable alongside language-assignable
  // ones, so split the current selection by flag — each half gets its own bulk
  // action (you can't assign a language to a track-mismatch, or swap a track on
  // a plain language row). Rows resolve straight from the server page
  // (data.items), which is already the complete filtered/grouped result (#494)
  // — there is no separate client-side filtered view left to drift from.
  const { selTmItems, selAssignPaths, selMultiRows } = useMemo(() => {
    const items = data?.items || [];
    const sel = items.filter((it) => epSelection.has(it.file_canonical_path || it.canonical_path));
    return {
      selTmItems: sel.filter((it) => it.flag === 'track_mismatch'),
      // #406: multilingual rows are deliberately included here too, so the
      // toolbar can RE-ASSIGN them (correct to single/different set) — they also
      // appear in selMultiRows for the Accept action. One click fires one action.
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
    // P2-S6: one bulk flow at a time. bulkRunning flags this language/multilingual
    // batch; busyPath flags per-row track ops AND track-mismatch bulk swap/dismiss
    // (as '__bulk__'). Bail before prompting so two bulk POSTs can't interleave.
    if (busyPath || bulkRunning) return;
    const paths = selAssignPaths;
    if (!paths.length) return;
    // #457: ONE source of truth for what gets assigned. This same array is
    // handed to buildVerifyBody below, so the dialog cannot drift from the
    // action it is describing.
    const assignCodes = multilingualMode ? bulkLangs : [bulkLang];
    const confirmText = bulkAssignConfirmText(assignCodes, paths.length);
    if (!confirmText) {
      window.alert('Pick at least one audio language first.');
      return;
    }
    if (!window.confirm(confirmText)) return;
    setBulkRunning(true);
    setBulkProgress({ done: 0, total: paths.length, errors: 0 });
    // #494 P2-S5/P3-S3: delegate to the shared runVerifyBatch runner — it arms
    // and clears the bulk-in-flight gate, emits one global audio-lang-verified
    // event per successful file, and performs exactly ONE authoritative silent
    // refetch after the whole batch (see shouldRefetchAfterVerify / the comment
    // on runVerifyBatch).
    await runVerifyBatch({
      items: paths,
      total: paths.length,
      // #406: multilingual mode submits the full checked set; otherwise the
      // single bulkLang. Empty selection -> builder returns null -> runner skips.
      submit: async (p) => {
        const verifyBody = buildVerifyBody(p, assignCodes);
        if (!verifyBody) return null;
        const r = await fetch('/api/audio-lang/verifications', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({ ...verifyBody, confidence: 1.0, evidence: { bulk: true } }),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return verifyBody;
      },
      pathOf: (p) => p,
      // Dispatch the global verified event for arena/other consumers. Review's own
      // listener is gated off for this batch's files (#494 P2-S5), so this single
      // dispatch does NOT trigger a per-file Review refetch here. #406: dispatch
      // the ACTUAL assigned code (codes[0] in multi mode), not the single-select
      // bulkLang — arena listens and would otherwise tag a live sweep wrong.
      emitVerified: (p, body) => window.dispatchEvent(new CustomEvent('audio-lang-verified', {
        detail: { file_canonical_path: p, lang_code: body.lang_code },
      })),
      onProgress: (done, total, errors) => setBulkProgress({ done, total, errors }),
      setGate: (s) => { bulkGateRef.current = s; },
      clearGate: () => { bulkGateRef.current = null; },
      finish: () => { setBulkRunning(false); clearSelection(); },
      // #226: if requested, declare one durable intent rule per distinct
      // series/movie in the selection. Best-effort — failures here never fail
      // the per-file bulk above (the primary action); they bump the error count.
      // Runs inside the gate-guarded try (before the gate clears) as in the
      // original hand-rolled loop.
      afterBatch: async (ctx) => {
        if (!rememberFuture || multilingualMode) return;
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
            ctx.errors += 1;
            setBulkProgress({ done: ctx.done, total: paths.length, errors: ctx.errors });
          }
        }
      },
      refetchAfterBatch: () => fetchPending({ silent: true }),
    });
  }, [selAssignPaths, bulkLang, multilingualMode, bulkLangs, fetchPending, clearSelection, rememberFuture, data, busyPath, bulkRunning]);

  // #406: "Accept (keep as detected)" — confirm each selected multilingual row's
  // OWN detected set as a user verdict (source='user'). Per-row (each carries its
  // own lang_codes), distinct from the uniform bulk-assign. Reuses the 4-worker
  // progress pattern; rows missing lang_codes are skipped (builder returns null).
  const acceptSelected = useCallback(async () => {
    // P2-S6: single bulk flow guard (see applyBulk). busyPath covers per-row and
    // track-bulk ops; bulkRunning covers language/multilingual batches.
    if (busyPath || bulkRunning) return;
    const rows = selMultiRows;
    if (!rows.length) return;
    setBulkRunning(true);
    setBulkProgress({ done: 0, total: rows.length, errors: 0 });
    // #494 P2-S5/P3-S3: same shared runVerifyBatch mechanics as applyBulk (gate
    // arm/clear + one global event per successful file + one final refetch).
    await runVerifyBatch({
      items: rows,
      total: rows.length,
      // Re-submit each row's OWN detected set as a user verdict. Rows missing
      // lang_codes return null -> runner skips them (counted done, no fetch/emit).
      submit: async (row) => {
        const body = acceptMultilingualBody(row);
        if (!body) return null;
        const r = await fetch('/api/audio-lang/verifications', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({ ...body, confidence: 1.0, evidence: { accept_multi: true } }),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return body;
      },
      pathOf: (row) => row.file_canonical_path || row.canonical_path,
      emitVerified: (p, body) => window.dispatchEvent(new CustomEvent('audio-lang-verified', {
        detail: { file_canonical_path: p, lang_code: body.lang_code },
      })),
      onProgress: (done, total, errors) => setBulkProgress({ done, total, errors }),
      setGate: (s) => { bulkGateRef.current = s; },
      clearGate: () => { bulkGateRef.current = null; },
      finish: () => { setBulkRunning(false); clearSelection(); },
      refetchAfterBatch: () => fetchPending({ silent: true }),
    });
  }, [selMultiRows, fetchPending, clearSelection, busyPath, bulkRunning]);

  const filterPills = [
    { id: 'all',     label: `all (${totalCounts.all})` },
    { id: 'suspect', label: `suspect (${totalCounts.suspect})` },
    { id: 'unknown', label: `unknown (${totalCounts.unknown})` },
    { id: 'track_mismatch', label: `track mismatch (${totalCounts.track_mismatch})` },
    { id: 'multilingual', label: `multilingual (${totalCounts.multilingual})` },  // #406
  ];
  const selectedCount = epSelection.size;
  // #494 P1-S2: Review pages GROUPS. group_count drives page number/range/prev/
  // next; data.count (matching files) stays available for the truthful file
  // totals shown alongside the group labels.
  const pagination = computePagination({
    count: data?.count, groupCount: data?.group_count, limit, offset, grouped: true,
  });

  return (
    <main className="main-canvas" style={{
      padding: '22px 24px 22px', gap: 14, overflow: 'hidden',
      display: 'flex', flexDirection: 'column',
    }}>
      <OrphanBanner />
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
            <span key={f.id} onClick={() => { setFilter(f.id); setOffset(0); }}
              role="button" tabIndex={0}
              aria-label={`Filter by ${f.id}`}
              onKeyDown={(e) => { if (e.key === 'Enter') { setFilter(f.id); setOffset(0); } }}
              className={`chip ${filter === f.id ? 'violet' : ''}`}
              style={{ cursor: 'pointer' }}>
              {f.label}
            </span>
          ))}
        </div>
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }} className="num"
          title="Pagination is by group; files shown are the complete matching files of the groups on this page.">
          {groups.length} {groups.length === 1 ? 'group' : 'groups'} ·{' '}
          {pageFileCount} of {pagination.fileCount} matching file{pagination.fileCount === 1 ? '' : 's'} shown
        </span>
      </div>

      {/* P3-S2 / #494: server-side pagination now pages COMPLETE GROUPS. Group
          range, page number, and prev/next boundaries come from the server's
          group_count, and the page-size selector is groups per page (limit still
          means "rows/offset slots", but the server addresses those slots to
          groups). Changing the page size resets to the first group page. Buttons
          are disabled mid-refetch so a pending response can't be double-stepped
          past the end. */}
      {data && pagination.total > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '2px 2px 6px' }}>
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>
            Group {pagination.shownStart}–{pagination.shownEnd} of {pagination.groupCount}
            <span style={{ color: 'var(--fg-3)', marginLeft: 8 }}>
              · {pagination.fileCount} matching file{pagination.fileCount === 1 ? '' : 's'}
            </span>
          </span>
          <label style={{
            display: 'inline-flex', alignItems: 'center', gap: 6,
            fontSize: 'var(--text-xs)', color: 'var(--fg-3)',
          }}>
            Groups per page
            <select value={limit}
              onChange={(e) => { setLimit(Number(e.target.value)); setOffset(0); }}
              aria-label="Groups per page"
              style={{
                height: 24, padding: '0 6px', background: 'var(--bg-1)', color: 'var(--fg-0)',
                border: 'var(--border)', borderRadius: 'var(--radius-md)', fontSize: 'var(--text-2xs)',
              }}>
              {[50, 100, 200, 500].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <span style={{ flex: 1 }} />
          <button className="btn sm" disabled={!pagination.hasPrev || isRefetching}
            aria-label="Previous group page"
            onClick={() => setOffset(Math.max(0, offset - limit))}>
            ‹ Prev
          </button>
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
            Page {pagination.pageNumber} of {pagination.totalPages}
          </span>
          <button className="btn sm" disabled={!pagination.hasNext || isRefetching}
            aria-label="Next group page"
            onClick={() => setOffset(offset + limit)}>
            Next ›
          </button>
        </div>
      )}

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
              {filter === 'all' && !search.trim()
                ? "🎉 Nothing pending. Audio-language data looks clean across your library."
                : `No groups match the "${filter}" filter${search.trim() ? ' or your search' : ''}.`}
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
                    // #494: stable backend group key (never the title) — React
                    // identity and expansion state stay correct when duplicate
                    // display titles render as separate groups.
                    key={g.key}
                    series={g}
                    expanded={expandedSeries.has(g.key)}
                    onToggleExpand={() => toggleExpand(g.key)}
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
