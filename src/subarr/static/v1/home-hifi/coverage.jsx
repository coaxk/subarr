// Coverage — flat dense gap-list table.
//
// Wired to GET /api/coverage (60s server cache) — polled every 10s. The
// backend already de-dupes via cache, so the polling cost is minimal.
// Row queue + bulk queue post to /api/coverage/queue. Re-walk now calls
// /api/schedule/coverage_walk/run-now and then forces a fresh fetch.

import { Glyph, StatusDot, LangTag } from './atoms.jsx';

const { useState, useEffect, useMemo, useCallback } = React;

// ─── Live data hook ──────────────────────────────────────────────
// Returns { data, loading, error, refetch }. `data` is null on first
// paint and stays at the last successful payload on transient errors so
// the table doesn't flash empty between polls.
export function useLiveCoverage(intervalMs = 10000) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchOnce = useCallback(async (opts = {}) => {
    const { fresh = false, silent = false } = opts;
    if (!silent) setLoading(true);
    try {
      // Read user preference for wanted-langs filter — stored in localStorage
      // by the Coverage filter bar. Empty means "show everything Bazarr wants".
      const onlyLangs = (() => {
        try { return localStorage.getItem('subarr.only_wanted_langs') || ''; }
        catch { return ''; }
      })();
      const params = new URLSearchParams();
      if (fresh) params.set('fresh', 'true');
      if (onlyLangs) params.set('only_wanted_langs', onlyLangs);
      const url = '/api/coverage' + (params.toString() ? '?' + params.toString() : '');
      const r = await fetch(url, { credentials: 'same-origin' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      setData(d);
      setError(null);
    } catch (e) {
      // Only surface error if we have no data yet — once we have a
      // payload, transient failures stay silent (per #142 principle:
      // don't flash red when we're not confirmed-failed).
      setError(prev => (data ? prev : e));
      // eslint-disable-next-line no-console
      console.debug('coverage fetch failed:', e);
    } finally {
      if (!silent) setLoading(false);
    }
  }, [data]);

  useEffect(() => {
    let cancelled = false;
    let timer = null;
    async function tick() {
      if (cancelled) return;
      await fetchOnce({ silent: true });
      if (!cancelled) timer = setTimeout(tick, intervalMs);
    }
    // Initial fetch shows loading; subsequent ticks are silent.
    (async () => {
      await fetchOnce();
      if (!cancelled) timer = setTimeout(tick, intervalMs);
    })();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs]);

  // v1.1-O fix #197: optimistic chip update — patch local state immediately
  // on verification, then refetch in background. Chip turns green within
  // ~1 frame instead of waiting for the next 10s poll.
  useEffect(() => {
    const handler = (e) => {
      const detail = e.detail || {};
      const filePath = detail.file_canonical_path;
      const langCode = detail.lang_code;
      if (filePath && langCode) {
        setData(prev => {
          if (!prev) return prev;
          const items = prev.items.map(it => {
            if (it.file_canonical_path === filePath || it.canonical_path === filePath) {
              return {
                ...it,
                audio_langs: [langCode],
                audio_label_suspect: false,
                audio_label_unknown: false,
                audio_label_notes: [...(it.audio_label_notes || []), `user-confirmed: '${langCode}'`],
              };
            }
            return it;
          });
          return { ...prev, items };
        });
      }
      // Trigger a silent refetch a few seconds out so server state
      // catches up to the optimistic update.
      setTimeout(() => fetchOnce({ silent: true }), 2500);
    };
    window.addEventListener('audio-lang-verified', handler);
    return () => window.removeEventListener('audio-lang-verified', handler);
  }, [fetchOnce]);

  // Pref change (wanted-langs filter etc) → immediate silent refetch.
  useEffect(() => {
    const handler = () => fetchOnce({ silent: false });
    window.addEventListener('coverage-prefs-changed', handler);
    return () => window.removeEventListener('coverage-prefs-changed', handler);
  }, [fetchOnce]);

  return { data, loading, error, refetch: fetchOnce };
}

// ─── Normalize backend item → row shape used by the table ────────
function deriveReason(item) {
  if (!item.monitored) return 'unmonitored';
  // v1.1.1 #219: synthetic Bazarr-blind rows. Wins over bazarr-wanted
  // because the row exists *despite* Bazarr — that's the whole story.
  // After user verification, the metadata is no longer "mislabeled" in
  // our view — we know the truth. But Bazarr still doesn't (until its
  // next sync). Render that state as "awaiting-sync" so the chip
  // accurately tells the user "you fixed it; waiting on Bazarr now".
  if (item.bazarr_blind && item.audio_verified) return 'awaiting-bazarr-sync';
  if (item.bazarr_blind) return 'audio-mislabel';
  if (item.embedded_en === 'EN' || item.embedded_en === 'EN(SDH)') return 'embedded-only';
  if (item.bazarr && item.bazarr.episode_id) return 'bazarr-wanted';
  return 'no-track';
}

function formatEpisode(epNum) {
  if (!epNum) return '';
  // Backend ships "6x3" — render as S06E03.
  const m = String(epNum).match(/^(\d+)x(\d+)$/);
  if (!m) return String(epNum);
  return `S${m[1].padStart(2, '0')}E${m[2].padStart(2, '0')}`;
}

function langCodeFromName(name) {
  // Bazarr ships original_language as a human name. Best-effort 3-char code.
  if (!name) return '';
  const known = {
    english: 'eng', french: 'fre', spanish: 'spa', japanese: 'jpn',
    chinese: 'zho', italian: 'ita', russian: 'rus', vietnamese: 'vie',
    german: 'ger', korean: 'kor', portuguese: 'por', dutch: 'nld',
    polish: 'pol', turkish: 'tur', arabic: 'ara', hindi: 'hin',
  };
  return known[String(name).toLowerCase()] || String(name).slice(0, 3).toLowerCase();
}

function normalizeRow(item, idx, settleMinutes = 0) {
  const ep = item.media_type === 'episode' ? formatEpisode(item.episode_number) : '';
  // Score: backend uses 0–1000ish; map to /100 for display (cap 9.9).
  const score = Math.min(9.9, (item.score || 0) / 100);
  const audio = (item.audio_langs && item.audio_langs.length)
    ? item.audio_langs.join(',')
    : '—';
  const origLang = langCodeFromName(item.original_language);
  const missing = (item.bazarr && item.bazarr.missing_subtitles) || [];
  // FIX: WANTED column should ONLY show what Bazarr is missing — not
  // union with Sonarr's original_language. Including origLang made
  // every foreign show look like Bazarr was asking for double when in
  // fact Bazarr is only missing English. origLang is kept on the item
  // for score/classification but excluded from the user-facing chip.
  const langs = Array.from(new Set(missing.filter(Boolean)));
  return {
    id: item.canonical_path + '|' + (item.episode_number || idx),
    score,
    type: item.media_type === 'movie' ? 'mov' : 'tv',
    title: item.title || 'Untitled',
    ep,
    langs,
    mon: !!item.monitored,
    disk: !!item.has_sub_on_disk,
    emb: !!(item.embedded_en && item.embedded_en !== 'NONE'),
    audio,
    reason: deriveReason(item),
    orig_lang: origLang,                  // ISO 639-1, e.g. 'es', 'fr'
    orig_lang_name: item.original_language,  // for tooltip e.g. 'Spanish'
    size: '—',
    // v1.1-O / E / H / I — surface flags for UI chips & badges
    audio_label_suspect: !!item.audio_label_suspect,
    audio_label_unknown: !!item.audio_label_unknown,
    audio_label_notes: item.audio_label_notes || [],
    audio_verified: (item.audio_label_notes || []).some(n =>
      typeof n === 'string' && n.toLowerCase().startsWith('user-confirmed')),
    // How the audio language was determined (badge tier): user|whisper|plex|ffprobe|null
    audio_source: item.audio_source || null,
    now_playing: !!item.now_playing,
    just_imported: !!item.just_imported,
    airing_soon: !!item.airing_soon,
    // #117 settle-window: import time + the active window so the badge can
    // compute a LIVE "settling (Xm left)" countdown on each render.
    import_ts: item.import_ts || null,
    settle_minutes: settleMinutes || 0,
    // #140 mis-grouped series: flag + the foreign langs found, plus the series
    // dir (for the dismiss call) and this row's own high-trust detected lang
    // (for the per-episode "why" breakdown).
    series_mixed: !!item.series_mixed_languages,
    series_mixed_langs: item.series_mixed_langs || [],
    series_path: item.canonical_path || null,
    detected_lang: ((item.audio_source === 'user' || item.audio_source === 'whisper')
      && item.audio_langs && item.audio_langs.length) ? item.audio_langs[0] : null,
    // Probe-gate: only 'verified' rows are real, actionable gaps. 'unprobed'
    // and 'probe_failed' are bucketed separately (Analyzing / Couldn't
    // analyze) and never enter the gap table or bulk-select.
    vstate: item.verification_state || 'verified',
    // #79: forced-only embedded-EN whose connected subgen has
    // IGNORE_FORCED_SUBTITLES OFF — subgen will SKIP it, so it's a distinct
    // non-actionable state (NOT a fillable gap). Routed to its own bucket
    // (visible, never silently hidden) instead of the gap table.
    forced_skip: !!item.forced_only_subgen_will_skip,
    // raw fields kept for action handlers
    _sonarr_episode_id: item.bazarr ? item.bazarr.episode_id : null,
    _canonical_path: item.file_canonical_path || item.canonical_path,
    _media_type: item.media_type,
  };
}

// ─── Demo dataset (fallback for design preview only) ────────────
const COVERAGE_ROWS = [
  { id: 1,  score: 9.4, type: 'tv',  title: 'Severance',                ep: 'S02E08', langs: ['eng','spa','fre'], mon: 1, disk: 0, emb: 0, audio: 'eng',     reason: 'no-track',      sel: true,  size: '4.2 GB' },
  { id: 2,  score: 9.2, type: 'tv',  title: 'Shogun',                   ep: 'S01E09', langs: ['eng','jpn'],       mon: 1, disk: 0, emb: 1, audio: 'jpn',     reason: 'embedded-only', sel: true,  size: '5.1 GB' },
  { id: 3,  score: 8.9, type: 'mov', title: 'Dune: Part Two',           ep: '',       langs: ['eng','fre'],       mon: 1, disk: 0, emb: 0, audio: 'eng',     reason: 'no-track',      sel: false, size: '20.4 GB' },
  { id: 4,  score: 8.6, type: 'tv',  title: 'Andor',                    ep: 'S01E04', langs: ['eng'],             mon: 1, disk: 0, emb: 0, audio: 'eng',     reason: 'bazarr-wanted', sel: true,  size: '3.8 GB' },
  { id: 5,  score: 8.4, type: 'tv',  title: 'Fallout',                  ep: 'S01E03', langs: ['eng'],             mon: 1, disk: 1, emb: 0, audio: 'eng',     reason: 'low-score',     sel: false, size: '4.0 GB' },
  { id: 6,  score: 8.2, type: 'mov', title: 'Anora',                    ep: '',       langs: ['eng','rus'],       mon: 1, disk: 0, emb: 0, audio: 'eng',     reason: 'no-track',      sel: true,  size: '6.8 GB' },
  { id: 7,  score: 7.9, type: 'tv',  title: '3 Body Problem',           ep: 'S01E05', langs: ['eng','zho'],       mon: 1, disk: 0, emb: 0, audio: 'eng',     reason: 'no-track',      sel: false, size: '3.6 GB' },
  { id: 8,  score: 7.6, type: 'mov', title: 'Furiosa: A Mad Max Saga',  ep: '',       langs: ['eng'],             mon: 1, disk: 0, emb: 1, audio: 'eng',     reason: 'embedded-only', sel: false, size: '14.2 GB' },
  { id: 9,  score: 7.4, type: 'tv',  title: 'House of the Dragon',      ep: 'S02E01', langs: ['eng'],             mon: 1, disk: 0, emb: 0, audio: 'eng',     reason: 'no-track',      sel: false, size: '4.4 GB' },
  { id: 10, score: 7.2, type: 'tv',  title: 'Ripley',                   ep: 'S01E03', langs: ['eng','ita'],       mon: 1, disk: 0, emb: 0, audio: 'eng,ita', reason: 'no-track',      sel: false, size: '2.9 GB' },
  { id: 11, score: 7.1, type: 'mov', title: 'The Bikeriders',           ep: '',       langs: ['eng'],             mon: 0, disk: 0, emb: 0, audio: 'eng',     reason: 'unmonitored',   sel: false, size: '5.2 GB' },
  { id: 12, score: 6.9, type: 'tv',  title: 'X-Men \u201997',           ep: 'S01E05', langs: ['eng','jpn'],       mon: 1, disk: 0, emb: 0, audio: 'eng',     reason: 'no-track',      sel: false, size: '1.4 GB' },
  { id: 13, score: 6.8, type: 'tv',  title: 'Ripley',                   ep: 'S01E02', langs: ['eng','ita'],       mon: 1, disk: 0, emb: 0, audio: 'eng,ita', reason: 'no-track',      sel: false, size: '2.7 GB' },
  { id: 14, score: 6.5, type: 'mov', title: 'Civil War',                ep: '',       langs: ['eng'],             mon: 1, disk: 0, emb: 0, audio: 'eng',     reason: 'no-track',      sel: false, size: '7.1 GB' },
  { id: 15, score: 6.3, type: 'tv',  title: 'Mr. & Mrs. Smith',         ep: 'S01E07', langs: ['eng'],             mon: 1, disk: 1, emb: 0, audio: 'eng',     reason: 'low-score',     sel: false, size: '3.8 GB' },
  { id: 16, score: 6.1, type: 'tv',  title: 'The Acolyte',              ep: 'S01E02', langs: ['eng'],             mon: 1, disk: 0, emb: 0, audio: 'eng',     reason: 'no-track',      sel: false, size: '4.1 GB' },
  { id: 17, score: 5.9, type: 'tv',  title: 'X-Men \u201997',           ep: 'S01E04', langs: ['eng','jpn'],       mon: 1, disk: 0, emb: 0, audio: 'eng',     reason: 'no-track',      sel: false, size: '1.4 GB' },
  { id: 18, score: 5.6, type: 'mov', title: 'Late Night with the Devil',ep: '',       langs: ['eng'],             mon: 1, disk: 0, emb: 0, audio: 'eng',     reason: 'no-track',      sel: false, size: '3.4 GB' },
  { id: 19, score: 5.2, type: 'tv',  title: 'Sugar',                    ep: 'S01E04', langs: ['eng'],             mon: 1, disk: 0, emb: 0, audio: 'eng',     reason: 'bazarr-wanted', sel: false, size: '2.1 GB' },
  { id: 20, score: 5.0, type: 'mov', title: 'Hit Man',                  ep: '',       langs: ['eng'],             mon: 1, disk: 1, emb: 0, audio: 'eng',     reason: 'low-score',     sel: false, size: '5.6 GB' },
  { id: 21, score: 4.9, type: 'tv',  title: 'The Sympathizer',          ep: 'S01E03', langs: ['eng','vie'],       mon: 1, disk: 0, emb: 0, audio: 'eng,vie', reason: 'no-track',      sel: false, size: '3.0 GB' },
  { id: 22, score: 4.7, type: 'tv',  title: 'Sugar',                    ep: 'S01E03', langs: ['eng'],             mon: 1, disk: 0, emb: 0, audio: 'eng',     reason: 'bazarr-wanted', sel: false, size: '2.1 GB' },
  { id: 23, score: 4.4, type: 'mov', title: 'Challengers',              ep: '',       langs: ['eng'],             mon: 1, disk: 0, emb: 1, audio: 'eng',     reason: 'embedded-only', sel: false, size: '5.8 GB' },
  { id: 24, score: 4.2, type: 'tv',  title: 'Dark Matter',              ep: 'S01E06', langs: ['eng'],             mon: 1, disk: 0, emb: 0, audio: 'eng',     reason: 'no-track',      sel: false, size: '3.3 GB' },
  { id: 25, score: 4.0, type: 'tv',  title: 'Bridgerton',               ep: 'S03E04', langs: ['eng','fre'],       mon: 0, disk: 0, emb: 0, audio: 'eng',     reason: 'unmonitored',   sel: false, size: '4.7 GB' },
  { id: 26, score: 3.8, type: 'mov', title: 'I.S.S.',                   ep: '',       langs: ['eng','rus'],       mon: 1, disk: 0, emb: 0, audio: 'eng',     reason: 'no-track',      sel: false, size: '4.4 GB' },
  { id: 27, score: 3.6, type: 'tv',  title: 'Star Wars: The Bad Batch', ep: 'S03E12', langs: ['eng'],             mon: 1, disk: 1, emb: 0, audio: 'eng',     reason: 'low-score',     sel: false, size: '1.6 GB' },
  { id: 28, score: 3.3, type: 'mov', title: 'Drive-Away Dolls',         ep: '',       langs: ['eng'],             mon: 1, disk: 0, emb: 0, audio: 'eng',     reason: 'no-track',      sel: false, size: '4.9 GB' },
  { id: 29, score: 3.1, type: 'tv',  title: 'Palm Royale',              ep: 'S01E08', langs: ['eng','spa'],       mon: 1, disk: 0, emb: 0, audio: 'eng',     reason: 'no-track',      sel: false, size: '2.8 GB' },
  { id: 30, score: 2.8, type: 'tv',  title: 'Resident Alien',           ep: 'S03E06', langs: ['eng'],             mon: 0, disk: 0, emb: 0, audio: 'eng',     reason: 'unmonitored',   sel: false, size: '2.0 GB' },
  { id: 31, score: 2.5, type: 'mov', title: 'Argylle',                  ep: '',       langs: ['eng'],             mon: 1, disk: 0, emb: 1, audio: 'eng',     reason: 'embedded-only', sel: false, size: '6.2 GB' },
  { id: 32, score: 2.1, type: 'tv',  title: 'Dr. Who',                  ep: 'S14E03', langs: ['eng'],             mon: 1, disk: 0, emb: 0, audio: 'eng',     reason: 'no-track',      sel: false, size: '2.3 GB' },
];

// Score colour gradient: violet (hi) → cyan (mid) → muted (lo).
function scoreColor(s) {
  if (s >= 8.5) return 'var(--violet-400)';
  if (s >= 7.0) return 'var(--violet-500)';
  if (s >= 5.5) return 'var(--cyan-500)';
  if (s >= 4.0) return 'var(--fg-1)';
  return 'var(--fg-2)';
}

const REASON_STYLE = {
  'no-track':       { fg: 'var(--error-500)',  bg: 'rgba(239,68,68,0.08)',  br: 'rgba(239,68,68,0.30)', label: 'no-track' },
  'embedded-only':  { fg: 'var(--warn-500)',   bg: 'rgba(245,158,11,0.08)', br: 'rgba(245,158,11,0.30)', label: 'embedded' },
  'bazarr-wanted':  { fg: 'var(--violet-400)', bg: 'rgba(139,92,246,0.10)', br: 'rgba(139,92,246,0.35)', label: 'wanted' },
  // v1.1.1 #219: rows Bazarr can't see — file metadata lies, Bazarr's
  // "audio matches subs" heuristic excludes them silently. Distinct
  // cyan tone so they stand out from regular wanted rows in the table.
  'audio-mislabel': { fg: 'var(--cyan-400)',   bg: 'rgba(34,211,238,0.10)', br: 'rgba(34,211,238,0.35)', label: 'mislabeled' },
  // v1.1.1 #219 closer: user verified, subarr propagated to Sonarr +
  // triggered Bazarr sync. Waiting for Bazarr to catch up. Calm violet
  // to signal "you've done your part; system is reconciling".
  'awaiting-bazarr-sync': { fg: 'var(--violet-400)', bg: 'rgba(139,92,246,0.08)', br: 'rgba(139,92,246,0.30)', label: 'syncing' },
  'low-score':      { fg: 'var(--fg-1)',       bg: 'var(--bg-2)',           br: 'var(--bg-4)',           label: 'low-score' },
  'unmonitored':    { fg: 'var(--fg-2)',       bg: 'transparent',           br: 'var(--bg-4)',           label: 'unmon' },
};

const REASON_TIPS = {
  'no-track':       'No subtitle track present and no audio metadata to even guess. Run probe or queue Whisper.',
  'embedded-only':  'File has an embedded English subtitle stream but no SRT sidecar. Plex/Apple TV need the sidecar.',
  'bazarr-wanted':  "Bazarr's wanted list put this in front of us — standard pipeline.",
  // v1.1.1 #219: distinct surface — explains *why* this row exists at all.
  'audio-mislabel': "Bazarr can't see this episode because the file's audio metadata claims English, but Sonarr says the series is in another language. Subarr surfaced it independently. Permanent fix: edit the series in Sonarr → Language Profile = the right language.",
  // v1.1.1 #219 closer: post-verification "we're done; waiting on Bazarr".
  'awaiting-bazarr-sync': "You verified the audio language. Subarr pushed the correction to Sonarr and triggered Bazarr's sync — this row will reappear under 'wanted' once Bazarr re-evaluates (typically a few seconds; longer if you have many series).",
  'low-score':      'Coverage signal exists but the score is below the queue threshold.',
  'unmonitored':    "Series is unmonitored in Sonarr — subarr won't queue these unless you force.",
};

function ReasonChip({ r }) {
  const v = REASON_STYLE[r] || REASON_STYLE['no-track'];
  const tip = REASON_TIPS[r];
  return (
    <span className="mono" title={tip || undefined} style={{
      display: 'inline-block', padding: '1px 7px',
      borderRadius: 2,
      border: `1px solid ${v.br}`, color: v.fg, background: v.bg,
      fontSize: 'var(--text-2xs)', lineHeight: '15px',
      letterSpacing: '0.01em',
      whiteSpace: 'nowrap',
      cursor: tip ? 'help' : 'default',
    }}>{v.label}</span>
  );
}

function TypeGlyph({ t }) {
  return (
    <span className="mono" style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-3)' }}>
      {t === 'tv' ? '\u25EB' : '\u25AD'}
    </span>
  );
}

function YesNo({ on, kind = 'muted' }) {
  if (on) return <StatusDot kind={kind} />;
  return <span style={{ color: 'var(--fg-3)', fontSize: 'var(--text-sm)' }}>·</span>;
}

function LangChips({ langs }) {
  const visible = langs.slice(0, 2);
  const overflow = langs.length - visible.length;
  return (
    <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
      {visible.map(l => (
        <span key={l} className="chip" style={{ height: 16, padding: '0 6px', fontSize: 'var(--text-2xs)' }}>
          <LangTag value={l} size={11} />
        </span>
      ))}
      {overflow > 0 && (
        <span className="num" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>+{overflow}</span>
      )}
    </div>
  );
}

// ─── Coverage strip ──────────────────────────────────────────────
function CoverageBar({ label, pct }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>{label}</span>
      <div style={{
        width: 96, height: 4,
        background: 'var(--bg-3)',
        borderRadius: 2,
        overflow: 'hidden',
      }}>
        <div style={{
          width: `${pct}%`, height: '100%',
          background: 'var(--violet-500)',
        }} />
      </div>
      <span className="num mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-1)', minWidth: 28 }}>{pct}%</span>
    </div>
  );
}

function fmtClock(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch { return '—'; }
}

function CoverageStrip({ data, loading, error }) {
  // Derive coverage % from upstream "sources" block when present. The
  // backend exposes counts (wanted vs total) per provider — we render a
  // dash if the field isn't populated for that provider rather than
  // making up a number.
  const sources = data?.sources || {};
  const bazarrCount = sources.bazarr?.wanted_episodes ?? sources.bazarr?.count ?? null;
  const sonarrCount = sources.sonarr?.series_count ?? sources.sonarr?.count ?? null;
  const radarrCount = sources.radarr?.movie_count ?? sources.radarr?.count ?? null;
  const totalGaps = data?.totals?.items ?? null;

  let statusLabel;
  if (error && !data) {
    statusLabel = <span style={{ color: 'var(--error-500)' }}>backend unreachable</span>;
  } else if (loading && !data) {
    statusLabel = <span style={{ color: 'var(--warn-500)' }}>loading…</span>;
  } else if (totalGaps === 0) {
    statusLabel = <span style={{ color: 'var(--ok-500, var(--violet-400))' }}>no gaps to address</span>;
  } else {
    statusLabel = <>
      <span style={{ color: 'var(--fg-0)', fontWeight: 600 }}>{totalGaps ?? '—'}</span> gaps
    </>;
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 18, flexWrap: 'wrap' }}>
      <span className="label">coverage</span>
      <span className="num" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
        bazarr <span style={{ color: 'var(--fg-0)' }}>{bazarrCount ?? '—'}</span>
      </span>
      <span className="num" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
        sonarr <span style={{ color: 'var(--fg-0)' }}>{sonarrCount ?? '—'}</span>
      </span>
      <span className="num" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
        radarr <span style={{ color: 'var(--fg-0)' }}>{radarrCount ?? '—'}</span>
      </span>
      <span style={{ width: 1, height: 14, background: 'var(--bg-4)' }} />
      <span className="num" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
        {statusLabel} · refreshed <span className="mono">{fmtClock(data?.generated_at)}</span>
        {data?.cached ? <span style={{ color: 'var(--fg-3)' }}> (cache {data.cache_age_s}s)</span> : null}
      </span>
    </div>
  );
}

// ─── Filter bar ──────────────────────────────────────────────────
function FilterChip({ children, active, onClose }) {
  return (
    <span className={`chip ${active ? 'violet' : ''}`} style={{
      height: 22, padding: '0 9px', fontSize: 'var(--text-xs)',
      letterSpacing: 0,
      cursor: 'default',
    }}>
      {children}
      {onClose && (
        <span style={{ marginLeft: 4, color: active ? 'var(--violet-400)' : 'var(--fg-3)', cursor: 'pointer' }}>×</span>
      )}
    </span>
  );
}

const REASON_FILTERS = ['all', 'no-track', 'bazarr-wanted', 'audio-mislabel', 'awaiting-bazarr-sync', 'embedded-only', 'low-score', 'unmonitored'];

// Languages I care about — persisted in localStorage so it survives reloads.
// Reads on every fetch in useLiveCoverage. Empty string = show everything
// Bazarr wants. UI shows a chip+popover for picking. Defaults to empty,
// user opts in when they want subarr to filter Bazarr's wanted list down.
function WantedLangsChip() {
  const [pref, setPref] = React.useState('');
  const [open, setOpen] = React.useState(false);
  const popRef = React.useRef(null);
  React.useEffect(() => {
    try { setPref(localStorage.getItem('subarr.only_wanted_langs') || ''); } catch {}
  }, []);
  React.useEffect(() => {
    if (!open) return;
    const close = (e) => { if (popRef.current && !popRef.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);
  const set = (v) => {
    try {
      if (v) localStorage.setItem('subarr.only_wanted_langs', v);
      else localStorage.removeItem('subarr.only_wanted_langs');
    } catch {}
    setPref(v);
    // Trigger a refetch — coverage page polls every 10s, but this
    // gives instant feedback.
    window.dispatchEvent(new CustomEvent('coverage-prefs-changed'));
  };
  const active = !!pref;
  const label = active ? `wanted: ${pref}` : 'all wanted langs';
  const COMMON = ['en', 'en,es', 'en,fr', 'en,de', 'en,ja', 'en,zh'];
  return (
    <span ref={popRef} style={{ position: 'relative', cursor: 'pointer' }}>
      <span onClick={() => setOpen(o => !o)}
        title={active
          ? `Only showing rows where Bazarr is asking for: ${pref}. Click to change.`
          : 'Click to filter the coverage list to only the subtitle languages you care about.'}>
        <FilterChip active={active}>{label}</FilterChip>
      </span>
      {open && (
        <div style={{
          position: 'absolute', top: 28, left: 0, zIndex: 10,
          background: 'var(--bg-1)', border: 'var(--border)',
          borderRadius: 'var(--radius-md)', padding: 10,
          minWidth: 220,
          boxShadow: '0 6px 16px rgba(0,0,0,0.4)',
          display: 'flex', flexDirection: 'column', gap: 6,
        }}>
          <div style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Show only rows wanting…
          </div>
          {COMMON.map(opt => (
            <button key={opt} onClick={() => { set(opt); setOpen(false); }}
              className={`chip ${pref === opt ? 'violet' : ''}`}
              style={{ cursor: 'pointer', height: 24, padding: '0 10px', textAlign: 'left' }}>
              {opt}
            </button>
          ))}
          <input type="text" placeholder="custom (e.g. en,pt)"
            defaultValue={pref}
            onKeyDown={(e) => { if (e.key === 'Enter') { set(e.target.value.trim()); setOpen(false); } }}
            style={{
              marginTop: 4, padding: '4px 8px',
              background: 'var(--bg-0)', border: 'var(--border)',
              borderRadius: 4, color: 'var(--fg-0)', fontSize: 'var(--text-xs)',
            }} />
          {active && (
            <button onClick={() => { set(''); setOpen(false); }}
              style={{
                marginTop: 4, padding: '4px 8px',
                background: 'transparent', border: '1px solid var(--bg-4)',
                borderRadius: 4, color: 'var(--fg-2)', fontSize: 'var(--text-2xs)',
                cursor: 'pointer',
              }}>clear · show all</button>
          )}
          <div style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)', marginTop: 4 }}>
            Persists locally. Bazarr is still asked for everything — subarr just hides what you don&apos;t care about.
          </div>
        </div>
      )}
    </span>
  );
}

function FilterBar({ groupBy, setGroupBy, filtered, reasonFilter, setReasonFilter, typeFilter, setTypeFilter, monitoredOnly, setMonitoredOnly }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 0' }}>
      {/* Search */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        height: 28, padding: '0 10px',
        background: 'var(--bg-2)',
        border: 'var(--border)',
        borderRadius: 'var(--radius-md)',
        width: 220,
      }}>
        <Glyph char="⌕" size={12} color="var(--fg-3)" />
        <span style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-3)' }}>Search title or path…</span>
      </div>

      {/* Active filter chips */}
      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <span
          onClick={() => setMonitoredOnly && setMonitoredOnly(!monitoredOnly)}
          style={{ cursor: 'pointer' }}>
          <FilterChip active={monitoredOnly}>monitored</FilterChip>
        </span>
        {REASON_FILTERS.map(r => (
          <span key={r} onClick={() => setReasonFilter && setReasonFilter(r)} style={{ cursor: 'pointer' }}>
            <FilterChip active={reasonFilter === r}>{r === 'all' ? 'all reasons' : r}</FilterChip>
          </span>
        ))}
        {['all', 'tv', 'mov'].map(t => (
          <span key={t} onClick={() => setTypeFilter && setTypeFilter(t)} style={{ cursor: 'pointer' }}>
            <FilterChip active={typeFilter === t}>{t === 'all' ? 'all types' : `type: ${t}`}</FilterChip>
          </span>
        ))}
        <WantedLangsChip />
      </div>

      <span style={{ flex: 1 }} />

      {/* Group-by toggle */}
      <div style={{
        display: 'flex',
        border: 'var(--border)',
        borderRadius: 'var(--radius-md)',
        overflow: 'hidden',
        background: 'var(--bg-1)',
      }}>
        {['flat', 'tree'].map((g, i) => {
          const active = g === groupBy;
          return (
            <button key={g} onClick={() => setGroupBy && setGroupBy(g)} style={{
              padding: '0 12px', height: 28,
              fontSize: 'var(--text-sm)',
              color: active ? 'var(--fg-0)' : 'var(--fg-2)',
              fontWeight: active ? 600 : 500,
              background: active ? 'var(--bg-3)' : 'transparent',
              borderLeft: i > 0 ? '1px solid var(--bg-4)' : 'none',
            }}>{g === 'flat' ? 'Flat' : 'Tree by show'}</button>
          );
        })}
      </div>

      <span className="num" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>
        {filtered} rows · sort: <span className="mono" style={{ color: 'var(--fg-2)' }}>score ↓</span>
      </span>
    </div>
  );
}

// ─── Table ───────────────────────────────────────────────────────
const COL = {
  check:  28,
  score:  44,
  type:   18,
  ep:     74,
  langs:  56,
  orig:   46,
  mon:    34,
  disk:   34,
  emb:    34,
  audio:  98,
  reason: 96,
  action: 60,
};

function HeaderCell({ children, w, right, center, tip }) {
  return (
    <div title={tip || undefined} style={{
      width: w, flex: w ? '0 0 auto' : 1, minWidth: 0,
      textAlign: right ? 'right' : center ? 'center' : 'left',
      fontSize: 'var(--text-2xs)',
      letterSpacing: '0.10em',
      textTransform: 'uppercase',
      color: 'var(--fg-2)',
      fontWeight: 600,
      cursor: tip ? 'help' : 'default',
      borderBottom: tip ? '1px dotted var(--fg-3)' : 'none',
    }}>{children}</div>
  );
}

// v1.1-O Layer 4 — small badge on the AUDIO cell showing the row's
// audio-language confidence state. Click handler hoisted by parent to
// open the review modal.
function AudioLabelChip({ r, onClick }) {
  // Trust-tiered: how do we know this row's audio language? A tick for the
  // verified tiers (colour = source), a muted marker for the file-tag-only
  // case, and the existing warn/unknown states. Tooltip explains each.
  let kind = null;
  if (r.audio_verified || r.audio_source === 'user') kind = 'user';
  else if (r.audio_source === 'whisper') kind = 'whisper';
  else if (r.audio_source === 'plex') kind = 'plex';
  else if (r.audio_label_suspect) kind = 'suspect';
  else if (r.audio_label_unknown) kind = 'unknown';
  else if (r.audio_source === 'ffprobe') kind = 'ffprobe';
  if (!kind) return null;
  const cfg = {
    user:    { ch: '✓', bg: 'rgba(34,211,161,0.18)',  fg: '#22d3a1', label: 'You verified this audio language' },
    whisper: { ch: '✓', bg: 'rgba(34,211,238,0.16)',  fg: '#22d3ee', label: 'System-verified by Whisper (multi-chunk language detection)' },
    plex:    { ch: '✓', bg: 'rgba(139,92,246,0.16)',  fg: '#a78bfa', label: 'From your Plex audio-track pick' },
    ffprobe: { ch: '~', bg: 'rgba(148,163,184,0.12)', fg: '#94a3b8', label: "From the file's metadata tag only — unverified (tags are often wrong on retags)" },
    suspect: { ch: '⚠', bg: 'rgba(245,158,11,0.18)', fg: '#f59e0b', label: 'Audio label looks wrong (foreign title tagged as English)' },
    unknown: { ch: '?', bg: 'rgba(148,163,184,0.18)', fg: '#94a3b8', label: 'No audio language metadata on the file' },
  }[kind];
  const evidence = (r.audio_label_notes || []).join('\n• ');
  const mismatch = !!r.audio_label_whisper_mismatch;  // #90: tag ≠ detected audio
  const tip = `${cfg.label}`
    + (mismatch ? '\n\n⚠ Tag mismatch — this file is tagged a different language than its audio.' : '')
    + (evidence ? '\n\n• ' + evidence : '')
    + '\n\nClick to verify/correct.';
  const badge = (
    <span
      title={tip}
      onClick={(e) => { e.stopPropagation(); onClick && onClick(r); }}
      style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        width: 16, height: 16,
        borderRadius: 4,
        background: cfg.bg,
        color: cfg.fg,
        fontSize: 10,
        fontWeight: 700,
        marginLeft: 6,
        cursor: 'pointer',
        flex: '0 0 auto',
      }}>{cfg.ch}</span>
  );
  if (!mismatch) return badge;
  // small amber corner dot — the tag-vs-audio mismatch signal (#90)
  return (
    <span style={{ position: 'relative', display: 'inline-flex', flex: '0 0 auto' }}>
      {badge}
      <span title="tag ≠ detected audio language" style={{
        position: 'absolute', top: -2, right: -2, width: 7, height: 7,
        borderRadius: '50%', background: '#f59e0b', border: '1px solid var(--bg-1)',
      }} />
    </span>
  );
}

// Three contextual badges that show next to the title: NOW PLAYING,
// JUST ADDED (imported <24h), AIRS SOON (<48h). High-signal nudges.
function ScoringBadges({ r }) {
  const badges = [];
  if (r.now_playing) {
    badges.push({
      key: 'np',
      label: 'NOW PLAYING',
      bg: 'rgba(234,179,8,0.18)',
      fg: '#facc15',
      tip: 'Currently streaming on Plex. +2000 score boost. Highest priority — get this sub done now.',
      pulse: true,
    });
  }
  if (r.just_imported) {
    badges.push({
      key: 'ji',
      label: 'JUST ADDED',
      bg: 'rgba(34,211,161,0.18)',
      fg: '#22d3a1',
      tip: 'Sonarr/Radarr imported this file in the last 24 hours. +800 score boost — watch likelihood peaks now.',
    });
  }
  if (r.airing_soon) {
    badges.push({
      key: 'as',
      label: 'AIRS SOON',
      bg: 'rgba(56,189,248,0.18)',
      fg: '#38bdf8',
      tip: 'Sonarr says this episode airs within 48 hours. +400 score boost — pre-warm the queue.',
    });
  }
  // #117 settle-window: freshly-imported gap being held out of auto-queue.
  // Computed live (Date.now) from import_ts + the active settle window so the
  // countdown ticks down across renders. Marks the row as deliberate waiting,
  // not inaction. Manual transcribe still works during the window.
  if (r.settle_minutes > 0 && r.import_ts) {
    const left = Math.ceil((r.import_ts + r.settle_minutes * 60 - Date.now() / 1000) / 60);
    if (left > 0) {
      badges.push({
        key: 'settle',
        label: `SETTLING ${left}m`,
        bg: 'rgba(148,163,184,0.18)',
        fg: '#94a3b8',
        tip: `Imported recently — auto-transcribe is held for ~${left} more min so Bazarr/providers can land a real sub first. Manual transcribe still works now.`,
      });
    }
  }
  // #140: this episode belongs to a series flagged as mixed-language (likely
  // two different shows merged — wrong downloads). Series-level signal shown
  // per-row so it's visible in flat view too; the tree view adds a dismissable
  // notice with the full per-episode breakdown.
  if (r.series_mixed) {
    const langs = (r.series_mixed_langs || []).join(', ');
    badges.push({
      key: 'mixed',
      label: '⚠ MIXED SERIES',
      bg: 'rgba(239,68,68,0.16)',
      fg: '#f87171',
      tip: `This series resolves to multiple distinct foreign spoken languages (${langs}) — likely two different shows merged into one Sonarr series (wrong downloads). Check the episodes.`,
    });
  }
  if (!badges.length) return null;
  return (
    <span style={{ display: 'inline-flex', gap: 4, flex: '0 0 auto' }}>
      {badges.map(b => (
        <span key={b.key} title={b.tip} className={b.pulse ? 'pulse-badge' : undefined}
          style={{
            display: 'inline-flex', alignItems: 'center',
            padding: '0 6px', height: 16,
            borderRadius: 3,
            background: b.bg,
            color: b.fg,
            fontSize: 9,
            fontWeight: 700,
            letterSpacing: '0.04em',
            textTransform: 'uppercase',
            cursor: 'help',
          }}>{b.label}</span>
      ))}
    </span>
  );
}

// v1.1-O Layer 4: banner showing how many rows need audio-lang review
// across the whole library. Polls /api/audio-lang/pending-review on mount
// and after a verification fires.
// ─── Friendly header — 4 panels above the dense filter+table UI ───
//
// Coverage is the most-trafficked page after Home. Drop the user into
// the same panel pattern the dashboard + rules pages use: 1 welcome
// card explaining what this page IS, plus a 4-tile status row that
// tells them at-a-glance what the situation looks like RIGHT NOW —
// before they touch the dense filter bar below.

function CoverageHeaderTile({ label, value, sub, tint, tip, accent }) {
  return (
    <div title={tip} style={{
      flex: 1, minWidth: 0,
      background: 'var(--bg-1)',
      border: 'var(--border)',
      borderRadius: 'var(--radius-lg)',
      padding: '12px 14px',
      display: 'flex', flexDirection: 'column', gap: 8,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {tint && <StatusDot kind={tint} />}
        <span className="label">{label}</span>
      </div>
      <div style={{
        fontSize: 'var(--text-h1)', lineHeight: 'var(--lh-h1)', fontWeight: 500,
        color: accent || 'var(--fg-0)', letterSpacing: '-0.01em',
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>{value}</div>
      <div style={{
        fontSize: 'var(--text-xs)', color: 'var(--fg-2)',
        minHeight: 16, lineHeight: 1.35,
      }}>{sub}</div>
    </div>
  );
}

function fmtRel(ts) {
  if (!ts) return null;
  const diff = Date.now() / 1000 - (typeof ts === 'string' ? Date.parse(ts) / 1000 : ts);
  if (diff < 60) return 'just now';
  const m = Math.floor(diff / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function CoverageStatusRow({ data, rows, pendingReview }) {
  // Read from the loaded coverage payload so the tiles auto-refresh on
  // every poll without an extra round-trip.
  // Probe-gate: "open gaps" = VERIFIED gaps (what's in the table). Un-probed
  // rows live in the Analyzing bucket, not the gap count, so the tile and
  // the table agree.
  const verif = data?.totals?.verification;
  const total = (verif ? verif.verified : data?.totals?.items) ?? null;
  const analyzing = verif?.unprobed ?? 0;
  const suspectAudio = rows.filter(r => r.reason === 'audio-mislabel').length;
  // "Worth a look right now" = high-score rows the user would most likely
  // actually want subtitles for. Threshold matches the auto-queue default.
  const worth = rows.filter(r => (r.score ?? 0) >= 200).length;
  const lastWalkTs = data?.generated_at;
  const lastWalkRel = fmtRel(lastWalkTs);
  const cached = data?.cached;
  const cacheAge = data?.cache_age_s;

  return (
    <div style={{ display: 'flex', gap: 12 }}>
      <CoverageHeaderTile
        label="open gaps"
        value={total == null ? '—' : total.toLocaleString('en-US')}
        sub={total === 0
          ? (analyzing ? `${analyzing} still being analyzed` : 'nothing missing right now')
          : (analyzing ? `in the table · ${analyzing} more analyzing` : 'rows in the table below')}
        tint={total > 0 ? 'warn' : 'ok'}
        tip="Verified gaps — files subarr has probed and confirmed are missing a sub. Un-probed files wait in the Analyzing bucket until checked."
      />
      <CoverageHeaderTile
        label="worth queueing"
        value={worth.toLocaleString('en-US')}
        sub={worth === 0
          ? 'no high-score candidates right now'
          : `score ≥ 200 — the auto-queue threshold`}
        tint={worth > 0 ? 'violet' : 'muted'}
        accent={worth > 0 ? 'var(--violet-400)' : undefined}
        tip="Rows scoring high enough that the default auto-queue rule would dispatch them. Visit Rules to change the threshold."
      />
      <CoverageHeaderTile
        label="needs your call"
        value={(suspectAudio + pendingReview).toLocaleString('en-US')}
        sub={[
          suspectAudio > 0 && `${suspectAudio} audio-suspect`,
          pendingReview > 0 && `${pendingReview} pending review`,
        ].filter(Boolean).join(' · ') || 'nothing waiting on you'}
        tint={(suspectAudio + pendingReview) > 0 ? 'warn' : 'muted'}
        tip="Rows where subarr can't decide on its own — usually because the audio language is mislabeled. Quick listen + confirm clears them."
      />
      <CoverageHeaderTile
        label="last walk"
        value={lastWalkRel || '—'}
        sub={cached
          ? `served from cache (${cacheAge}s old)`
          : (lastWalkTs ? 'fresh data' : 'use Re-walk to populate')}
        tint={lastWalkTs ? 'info' : 'muted'}
        tip="When subarr last reconciled gaps against the live arr stack. Re-walk now to refresh on demand."
      />
    </div>
  );
}

const COVERAGE_WELCOME_KEY = 'subarr.coverage.welcome.dismissed';
const COVERAGE_WELCOME_COLLAPSED_KEY = 'subarr.coverage.welcome.collapsed';

function CoverageWelcomeCard({ rows, pendingReview }) {
  // User feedback (2026-05-31): the original always-expanded card squashed
  // the gap-list table on Coverage. Two-tier dismiss now:
  //   - "got it" → dismissed entirely, no header bar (localStorage forever).
  //   - chevron → collapsed to a single 32px row showing the one-line
  //     intro + an "expand" affordance. Status tiles above always render;
  //     the welcome card is only visible on demand.
  // First-time visitors land on expanded so they get the orientation
  // message; the moment they expand/collapse via the chevron OR click any
  // quick-action, we flip collapsed=1 so the page stops eating space.
  const [dismissed, setDismissed] = useState(() => {
    try { return localStorage.getItem(COVERAGE_WELCOME_KEY) === '1'; }
    catch { return false; }
  });
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem(COVERAGE_WELCOME_COLLAPSED_KEY) === '1'; }
    catch { return false; }
  });
  if (dismissed) return null;
  const dismiss = () => {
    try { localStorage.setItem(COVERAGE_WELCOME_KEY, '1'); } catch {}
    setDismissed(true);
  };
  const persistCollapsed = (v) => {
    try { localStorage.setItem(COVERAGE_WELCOME_COLLAPSED_KEY, v ? '1' : '0'); } catch {}
    setCollapsed(v);
  };

  if (collapsed) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10,
        padding: '8px 14px',
        background: 'rgba(139,92,246,0.06)',
        border: '1px solid rgba(139,92,246,0.20)',
        borderRadius: 'var(--radius-md)',
        fontSize: 'var(--text-sm)', color: 'var(--fg-1)',
      }}>
        <span style={{ fontSize: 14, lineHeight: 1 }}>🗂️</span>
        <span style={{ flex: 1 }}>
          Coverage is the live gap list — score 0-1000, higher = more likely you want the sub.
        </span>
        <button className="btn ghost" onClick={() => persistCollapsed(false)}
          title="Expand the explainer + quick actions"
          style={{ fontSize: 'var(--text-2xs)' }}>
          ▾ expand
        </button>
        <button className="btn ghost" onClick={dismiss}
          title="Hide this card entirely on this device"
          aria-label="Dismiss welcome card"
          style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>×</button>
      </div>
    );
  }
  // The 3 quick-action cards pivot off what's actually in the data.
  const worth = rows.filter(r => (r.score ?? 0) >= 200).length;
  const steps = [
    {
      icon: '🎯',
      title: 'See the highest-priority gaps',
      copy: 'Filter the table to score ≥ 200 — the rows your auto-queue rule would dispatch first.',
      cta: { label: 'Filter by score', onClick: (e) => {
        e.preventDefault();
        // Open the score-floor filter — same hash anchor the FilterBar listens to.
        window.dispatchEvent(new CustomEvent('coverage-filter', { detail: { kind: 'score-floor', value: 200 } }));
      }},
    },
    pendingReview > 0 ? {
      icon: '🎧',
      title: `Review ${pendingReview} audio-language flags`,
      copy: 'A 20-second listen each unblocks subarr to either skip (English) or transcribe (foreign).',
      cta: { label: 'Open batch review', onClick: (e) => {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent('open-batch-review'));
      }},
    } : {
      icon: '🧠',
      title: 'How the score works',
      copy: 'Each row scores against monitored status, watch recency (Tautulli), and provider success rate. Higher = more likely you actually want the sub.',
      cta: { label: 'Read scoring docs', href: '/settings#scoring' },
    },
    {
      icon: '⚙',
      title: 'Edit auto-queue rules',
      copy: 'Decide what subarr does on every scheduled walk: off, dashboard-only, manual-confirm, or full auto-queue.',
      cta: { label: 'Open Rules', href: '/rules' },
    },
  ];
  return (
    <div style={{
      background: 'linear-gradient(135deg, rgba(139,92,246,0.10), rgba(34,211,161,0.05))',
      border: '1px solid rgba(139,92,246,0.30)',
      borderRadius: 'var(--radius-lg)',
      padding: '18px 20px',
      display: 'flex', flexDirection: 'column', gap: 14,
    }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <span style={{ fontSize: 22, lineHeight: 1 }}>🗂️</span>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 'var(--text-lg)', fontWeight: 600, color: 'var(--fg-0)' }}>
            Coverage is the live gap list.
          </div>
          <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)', marginTop: 4, lineHeight: 1.5 }}>
            Bazarr seeds it, then subarr reconciles against Sonarr, Radarr, the on-disk probe,
            and (where present) Tautulli watch history. Each row scores from <b>0</b> to <b>1000</b>;
            higher = more likely you actually want the sub. Pick rows + Queue, or
            tune the <a href="/rules" style={{ color: 'var(--violet-400)' }}>auto-queue rule</a>{' '}
            and let subarr do it.
          </div>
        </div>
        <button className="btn ghost" onClick={() => persistCollapsed(true)}
          title="Collapse to a single hint bar — quick actions still accessible."
          style={{ fontSize: 'var(--text-2xs)' }}>▴ collapse</button>
        <button className="btn ghost" onClick={dismiss}
          title="Hide this card entirely on this device."
          aria-label="Dismiss welcome card"
          style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>×</button>
      </div>
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
        gap: 10,
      }}>
        {steps.map((s, i) => (
          <div key={i} style={{
            background: 'var(--bg-1)', border: 'var(--border)',
            borderRadius: 'var(--radius-md)', padding: 12,
            display: 'flex', flexDirection: 'column', gap: 8,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 16 }}>{s.icon}</span>
              <span style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--fg-0)' }}>
                {s.title}
              </span>
            </div>
            <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)', flex: 1 }}>
              {s.copy}
            </div>
            <div>
              <a href={s.cta.href || '#'} onClick={(e) => {
                // Auto-collapse so the page returns to the data-first
                // layout after the user takes their first action.
                persistCollapsed(true);
                if (s.cta.onClick) s.cta.onClick(e);
              }}
                className="btn" style={{
                  textDecoration: 'none', display: 'inline-block',
                  fontSize: 'var(--text-2xs)', padding: '4px 10px',
                  background: 'var(--violet-500)', color: '#fff',
                }}>
                {s.cta.label}
              </a>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function PendingReviewBanner() {
  const [count, setCount] = useState(0);
  const [hidden, setHidden] = useState(false);
  const refetch = async () => {
    try {
      const r = await fetch('/api/audio-lang/pending-review', { credentials: 'same-origin' });
      if (!r.ok) return;
      const d = await r.json();
      setCount(d.count || 0);
    } catch {}
  };
  useEffect(() => {
    refetch();
    const handler = () => refetch();
    window.addEventListener('audio-lang-verified', handler);
    return () => window.removeEventListener('audio-lang-verified', handler);
  }, []);
  if (hidden || count === 0) return null;
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12,
      padding: '10px 14px',
      background: 'rgba(245,158,11,0.10)',
      border: '1px solid rgba(245,158,11,0.30)',
      borderRadius: 'var(--radius-md)',
      fontSize: 'var(--text-sm)',
      color: 'var(--fg-1)',
    }}>
      <span style={{ fontSize: 16 }}>⚠</span>
      <span style={{ flex: 1 }}>
        <b>{count}</b> file{count === 1 ? '' : 's'} need audio-language review.
        Click the ⚠ or ? badge on any row, or batch-review with the audio player.
      </span>
      <button className="btn" onClick={() => window.dispatchEvent(new CustomEvent('open-batch-review'))}
        style={{ background: 'var(--violet-500)', color: '#fff' }}>
        🎧 Review all ({count})
      </button>
      <button className="btn ghost" onClick={() => setHidden(true)}
        title="Hide for this session" style={{ fontSize: 'var(--text-2xs)' }}>dismiss</button>
    </div>
  );
}

// v1.1-O Layer 4++: batch review. Loads the pending-review list once,
// then walks the user through each item with audio player + Confirm /
// Correct / Skip. Burns through 50+ verifications in 10 min.
function BatchReviewModal() {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState([]);
  const [idx, setIdx] = useState(0);
  const [picked, setPicked] = useState('eng');
  const [posData, setPosData] = useState(null);
  const [posLoading, setPosLoading] = useState(false);
  const [activeSampleIdx, setActiveSampleIdx] = useState(0);
  const [track, setTrack] = useState(0);
  const [saving, setSaving] = useState(false);
  const [stats, setStats] = useState({ confirmed: 0, skipped: 0 });

  // Load pending list when opened.
  useEffect(() => {
    const handler = async () => {
      setOpen(true);
      setIdx(0);
      setStats({ confirmed: 0, skipped: 0 });
      try {
        const r = await fetch('/api/audio-lang/pending-review', { credentials: 'same-origin' });
        const d = await r.json();
        setItems(d.items || []);
      } catch {
        setItems([]);
      }
    };
    window.addEventListener('open-batch-review', handler);
    return () => window.removeEventListener('open-batch-review', handler);
  }, []);

  const cur = items[idx];

  // Fetch sample positions when item or track changes. NB the dep array
  // includes the resolved file path string (not `cur` itself) so the
  // effect fires when `items` loads async and cur becomes defined.
  const curPath = cur && (cur.file_canonical_path || cur.canonical_path);
  useEffect(() => {
    if (!open || !curPath) return;
    setPosData(null);
    setActiveSampleIdx(0);
    setPosLoading(true);
    setPicked('eng');
    let cancelled = false;
    fetch(`/api/audio-lang/sample-positions?canonical_path=${encodeURIComponent(curPath)}&track=${track}&n=3`)
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(d => { if (!cancelled) setPosData(d); })
      .catch(() => { if (!cancelled) setPosData(null); })
      .finally(() => { if (!cancelled) setPosLoading(false); });
    return () => { cancelled = true; };
  }, [open, curPath, track]);

  const close = () => {
    setOpen(false);
    setItems([]);
    setPosData(null);
    window.dispatchEvent(new CustomEvent('audio-lang-verified'));
  };
  const skip = () => {
    setStats(s => ({ ...s, skipped: s.skipped + 1 }));
    advance();
  };
  const advance = () => {
    if (idx + 1 >= items.length) {
      close();
    } else {
      setIdx(i => i + 1);
      setTrack(0);
    }
  };
  const confirmAndNext = async () => {
    if (!cur) return;
    setSaving(true);
    try {
      await fetch('/api/audio-lang/verifications', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          canonical_path: cur.file_canonical_path || cur.canonical_path,
          lang_code: picked,
          source: 'user',
          confidence: 1.0,
          evidence: { notes: cur.notes || [], track, batch: true },
        }),
      });
      setStats(s => ({ ...s, confirmed: s.confirmed + 1 }));
      advance();
    } catch (e) {
      alert('Save failed: ' + (e.message || e));
    } finally {
      setSaving(false);
    }
  };

  if (!open) return null;
  const fmtT = (s) => {
    if (s == null) return '—';
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${String(sec).padStart(2, '0')}`;
  };
  const sampleUrl = posData && cur && posData.positions && posData.positions[activeSampleIdx] != null
    ? `/api/audio-lang/sample?canonical_path=${encodeURIComponent(cur.file_canonical_path || cur.canonical_path)}&start=${posData.positions[activeSampleIdx]}&duration=12&track=${track}`
    : null;

  return (
    <div onClick={close} style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.65)', zIndex: 110,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <div onClick={(e) => e.stopPropagation()} className="panel" style={{
        width: 640, maxWidth: '94vw', maxHeight: '92vh',
        padding: 20, display: 'flex', flexDirection: 'column', gap: 14,
        overflowY: 'auto',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <div style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)', letterSpacing: '0.04em', textTransform: 'uppercase' }}>
              Batch audio review
            </div>
            <div style={{ fontSize: 14, color: 'var(--fg-1)', marginTop: 4 }}>
              {idx + 1} of {items.length} ·
              <span style={{ color: 'var(--success-500, #22d3a1)', marginLeft: 6 }}>✓ {stats.confirmed}</span> ·
              <span style={{ color: 'var(--fg-3)', marginLeft: 6 }}>skipped {stats.skipped}</span>
            </div>
          </div>
          <button className="btn ghost" onClick={close} style={{ fontSize: 'var(--text-2xs)' }}>
            close batch
          </button>
        </div>

        {/* Progress bar */}
        <div style={{ height: 4, background: 'var(--bg-3)', borderRadius: 2, overflow: 'hidden' }}>
          <div style={{
            width: `${((idx) / Math.max(1, items.length)) * 100}%`,
            height: '100%',
            background: 'linear-gradient(90deg,#22d3a1,#38bdf8,#8b5cf6)',
            transition: 'width 200ms ease',
          }} />
        </div>

        {!cur && <div style={{ padding: 30, textAlign: 'center', color: 'var(--fg-2)' }}>
          Loading review queue…
        </div>}

        {cur && (
          <>
            <div>
              <div style={{ fontSize: 18, fontWeight: 600, color: 'var(--fg-0)' }}>
                {cur.title}{cur.episode_number ? ` · ${cur.episode_number}` : ''}
              </div>
              <div className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)', marginTop: 4, wordBreak: 'break-all' }}>
                {cur.file_canonical_path || cur.canonical_path}
              </div>
              <div style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)', marginTop: 4 }}>
                Flag: <b style={{ color: cur.flag === 'suspect' ? '#f59e0b' : '#94a3b8' }}>{cur.flag}</b> ·
                ffprobe says: {(cur.audio_langs || []).join(',') || '—'} ·
                Sonarr says original: {cur.original_language || '—'}
              </div>
            </div>

            <div style={{ background: 'var(--bg-1)', borderRadius: 'var(--radius-md)', padding: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)', fontWeight: 600 }}>🎧 Listen</span>
                {posData && posData.audio_tracks > 1 && (
                  <>
                    <span style={{ color: 'var(--bg-5)' }}>·</span>
                    <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>track</span>
                    {Array.from({ length: posData.audio_tracks }).map((_, i) => (
                      <button key={i} onClick={() => setTrack(i)}
                        className={`chip ${track === i ? 'violet' : ''}`}
                        style={{ height: 22, padding: '0 8px', fontSize: 'var(--text-2xs)', cursor: 'pointer' }}>
                        {i}
                      </button>
                    ))}
                  </>
                )}
                <span style={{ flex: 1 }} />
                {posData && posData.method === 'vad' && (
                  <span title="Clips picked by detecting actual speech (silero VAD), not just non-silence"
                    style={{ fontSize: 'var(--text-2xs)', color: 'var(--violet)', whiteSpace: 'nowrap', marginRight: 6 }}>
                    🎙 speech-detected
                  </span>
                )}
                {posData && <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>
                  {fmtT(posData.duration_s)}
                </span>}
              </div>
              {posLoading && <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
                Scanning…
              </div>}
              {posData && posData.positions && posData.positions.length > 0 && (
                <>
                  <div style={{ display: 'flex', gap: 6 }}>
                    {posData.positions.map((p, i) => (
                      <button key={i} onClick={() => setActiveSampleIdx(i)}
                        className={`chip ${activeSampleIdx === i ? 'violet' : ''}`}
                        style={{ flex: 1, justifyContent: 'center', cursor: 'pointer', padding: '4px 8px' }}>
                        {i + 1} · {fmtT(p)}
                      </button>
                    ))}
                  </div>
                  {sampleUrl && (
                    <audio key={sampleUrl} controls autoPlay src={sampleUrl} style={{ width: '100%', height: 36 }} />
                  )}
                </>
              )}
            </div>

            <div>
              <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)', marginBottom: 6 }}>
                Language for track {track}:
              </div>
              <select value={picked} onChange={(e) => setPicked(e.target.value)}
                style={{ width: '100%', padding: '8px 10px', background: 'var(--bg-1)',
                         color: 'var(--fg-0)', border: 'var(--border)', borderRadius: 'var(--radius-md)' }}>
                {LANG_PICKS.map(([c, n]) => <option key={c} value={c}>{n} ({c})</option>)}
              </select>
            </div>

            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn ghost" onClick={skip} disabled={saving} style={{ flex: 1 }}>
                Skip →
              </button>
              <button data-testid="review-confirm" className="btn" onClick={confirmAndNext} disabled={saving}
                style={{ flex: 2, background: 'var(--violet-500)', color: '#fff' }}>
                {saving ? 'Saving…' : `Confirm ${picked} → Next`}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// v1.1-F Arbiter dialog: before queueing Whisper, ask Bazarr's providers
// what human subs are available. Triggered by row "Bazarr?" button →
// CustomEvent('open-arbiter') from CoverageRow.
function ArbiterModal() {
  const [row, setRow] = useState(null);
  const [candidates, setCandidates] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [acting, setActing] = useState(false);
  useEffect(() => {
    const handler = (e) => {
      setRow(e.detail);
      setCandidates(null);
      setError(null);
      setLoading(true);
      const id = e.detail?._sonarr_episode_id;
      if (!id) {
        setLoading(false);
        setError('Row has no sonarr_episode_id — arbiter requires episode rows.');
        return;
      }
      fetch(`/api/arbiter/candidates?episode_id=${id}&language=en`,
            { credentials: 'same-origin' })
        .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
        .then(d => setCandidates(d))
        .catch(err => setError(String(err.message || err)))
        .finally(() => setLoading(false));
    };
    window.addEventListener('open-arbiter', handler);
    return () => window.removeEventListener('open-arbiter', handler);
  }, []);
  if (!row) return null;
  const close = () => { setRow(null); setCandidates(null); };
  const accept = async (c) => {
    setActing(true);
    try {
      const r = await fetch('/api/arbiter/accept', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          episode_id: row._sonarr_episode_id,
          language: 'en',
          provider: c.provider,
          subtitles_id: c.subtitle || c.subs_id || '',
          score: c.score || 0,
          forced: c.forced === 'True' || c.forced === true,
          hi: c.hearing_impaired === 'True' || c.hi === true,
        }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
      alert(`Bazarr is downloading: ${c.provider} (score ${c.score})`);
      close();
    } catch (e) { alert(`Accept failed: ${e.message}`); }
    finally { setActing(false); }
  };
  const tierColor = { excellent: '#22d3a1', decent: '#facc15', weak: '#ef4444' };
  return (
    <div onClick={close} style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 100,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <div onClick={(e) => e.stopPropagation()} className="panel" style={{
        width: 680, maxWidth: '94vw', maxHeight: '90vh',
        padding: 20, display: 'flex', flexDirection: 'column', gap: 14,
        overflowY: 'auto',
      }}>
        <div>
          <div style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)', letterSpacing: '0.04em', textTransform: 'uppercase' }}>
            Whisper-or-Bazarr arbiter
          </div>
          <div style={{ fontSize: 18, fontWeight: 600, color: 'var(--fg-0)', marginTop: 4 }}>
            {row.title}{row.ep ? ` · ${row.ep}` : ''}
          </div>
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)', marginTop: 6 }}>
            Asking Bazarr&apos;s enabled providers if a human-translated sub already exists for this episode.
            If a strong candidate appears, prefer it over Whisper — saves GPU and (usually) gives better quality.
          </div>
        </div>

        {loading && (
          <div style={{ padding: 30, textAlign: 'center', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
            <span className="spinner-ring lg" />
            <div style={{ color: 'var(--fg-1)', fontSize: 'var(--text-sm)' }}>
              Searching providers…
            </div>
            <div style={{ color: 'var(--fg-3)', fontSize: 'var(--text-2xs)' }}>
              First call hits external providers — can take 30s. Subsequent searches for this episode are cached.
            </div>
          </div>
        )}
        {error && <div style={{ color: 'var(--error-500)', fontSize: 'var(--text-xs)' }}>{error}</div>}

        {candidates && candidates.candidates && candidates.candidates.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {candidates.candidates.slice(0, 5).map((c, i) => (
              <div key={i} style={{
                display: 'flex', alignItems: 'center', gap: 10,
                padding: 10, background: 'var(--bg-1)',
                border: '1px solid var(--bg-3)', borderRadius: 'var(--radius-md)',
              }}>
                <span style={{
                  padding: '2px 8px', borderRadius: 3,
                  background: 'var(--bg-3)',
                  color: tierColor[c.tier] || 'var(--fg-2)',
                  fontSize: 9, fontWeight: 700, textTransform: 'uppercase',
                  flex: '0 0 auto', letterSpacing: '0.04em',
                }}>{c.tier}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-0)', fontWeight: 500 }}>
                    {c.provider}
                  </div>
                  <div className="mono" style={{
                    fontSize: 'var(--text-2xs)', color: 'var(--fg-3)',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>{Array.isArray(c.release_info) ? c.release_info.join(', ') : (c.release_info || '')}</div>
                </div>
                <span className="num mono" style={{ color: 'var(--fg-1)', minWidth: 36, textAlign: 'right' }}>
                  {c.score}
                </span>
                <button className="btn sm" disabled={acting} onClick={() => accept(c)}
                  style={{ background: 'var(--violet-500)', color: '#fff' }}>
                  Take this
                </button>
              </div>
            ))}
          </div>
        )}

        {candidates && (!candidates.candidates || candidates.candidates.length === 0) && (
          <div style={{ padding: 16, textAlign: 'center', color: 'var(--fg-2)', fontSize: 'var(--text-sm)', display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div>No human-translated subtitle available from any provider.</div>
            <div style={{ color: 'var(--fg-3)', fontSize: 'var(--text-xs)' }}>
              {candidates.filtered_self_whisper
                ? `(${candidates.filtered_self_whisper} whisperai result${candidates.filtered_self_whisper === 1 ? '' : 's'} filtered — that's your own subgen.) Your best option is Whisper anyway.`
                : 'Your best option is Whisper anyway.'}
            </div>
          </div>
        )}

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 'auto' }}>
          <button className="btn ghost" onClick={close} disabled={acting}>cancel</button>
          <button className="btn" onClick={close}
            title="Skip arbiter, queue Whisper via the regular queue button">
            Whisper anyway →
          </button>
        </div>
      </div>
    </div>
  );
}

// v1.1-O Layer 4: per-row audio-lang verification modal. Triggered by
// CustomEvent('open-audio-review') from AudioLabelChip clicks.
// Alphabetical by English name. ISO 639-2 (3-letter) codes — they're
// what Bazarr / Sonarr / Plex all settle on internally, even when the
// UI shows ISO 639-1 (2-letter) codes elsewhere. Expanded 2026-05-31
// to cover the Balkan + Baltic + remaining EU slavic languages
// (missing Serbian / Bulgarian / Croatian was a hole flagged by Judd).
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
  ['ice','Icelandic'],
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
export function AudioReviewModal() {
  const [row, setRow] = useState(null);
  const [selected, setSelected] = useState('eng');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  // v1.1-O Layer 4++ audio player state
  const [posData, setPosData] = useState(null);
  const [posLoading, setPosLoading] = useState(false);
  const [track, setTrack] = useState(0);
  const [activeSampleIdx, setActiveSampleIdx] = useState(0);
  // v1.2-A Layer 3 (#179): Whisper robust detection result.
  // Sync subgen call; ~6-12s wall on RTX 3090. We don't auto-run on
  // open because it's expensive; user clicks the button when they
  // want the evidence. Result shape from /api/audio-lang/whisper-detect:
  //   { chunks: [{offset_s, language, language_name, probability}, ...],
  //     aggregate: {language, n_agreeing, n_total, min_probability} }
  const [whisperResult, setWhisperResult] = useState(null);
  const [whisperRunning, setWhisperRunning] = useState(false);
  const [whisperError, setWhisperError] = useState(null);

  useEffect(() => {
    const handler = (e) => {
      setRow(e.detail);
      setSelected((e.detail?.audio || 'eng').split(',')[0] || 'eng');
      setError(null);
      setPosData(null);
      setTrack(0);
      setActiveSampleIdx(0);
      setWhisperResult(null);
      setWhisperRunning(false);
      setWhisperError(null);
      // Fetch sample positions immediately so the player is ready.
      // #9: a 404 here means subarr's media_root view of the path
      // doesn't resolve to a real file (path-prefix mismatch between
      // subarr and Sonarr/Plex is the common cause). The user can
      // still confirm the language; the audio preview is a nice-to-have.
      // We surface 404 as posData={unavailable:true} so the player
      // area renders an amber "preview unavailable" notice instead of
      // a scary red error that blocks the eye, and we DO NOT call
      // setError (which is reserved for hard verify failures).
      if (e.detail?._canonical_path) {
        setPosLoading(true);
        fetch(`/api/audio-lang/sample-positions?canonical_path=${encodeURIComponent(e.detail._canonical_path)}&track=0&n=3`,
              { credentials: 'same-origin' })
          .then(async r => {
            if (r.ok) return r.json();
            if (r.status === 404) {
              const body = await r.json().catch(() => ({}));
              return { unavailable: true, reason: body?.detail || 'file not found at expected path' };
            }
            return Promise.reject(new Error(`HTTP ${r.status}`));
          })
          .then(setPosData)
          .catch(err => setPosData({ unavailable: true, reason: err.message || String(err) }))
          .finally(() => setPosLoading(false));
      }
    };
    window.addEventListener('open-audio-review', handler);
    return () => window.removeEventListener('open-audio-review', handler);
  }, []);

  // When user switches track, refetch positions for that track.
  useEffect(() => {
    if (!row || !posData) return;
    if (posData.track === track) return;
    setPosLoading(true);
    fetch(`/api/audio-lang/sample-positions?canonical_path=${encodeURIComponent(row._canonical_path)}&track=${track}&n=3`)
      .then(async r => {
        if (r.ok) return r.json();
        if (r.status === 404) {
          const body = await r.json().catch(() => ({}));
          return { unavailable: true, reason: body?.detail || 'file not found at expected path' };
        }
        return Promise.reject(new Error(`HTTP ${r.status}`));
      })
      .then(d => { setPosData(d); setActiveSampleIdx(0); })
      .catch(err => setPosData({ unavailable: true, reason: err.message || String(err) }))
      .finally(() => setPosLoading(false));
  }, [track]);

  if (!row) return null;
  const close = () => { setRow(null); setPosData(null); setWhisperResult(null); };
  // v1.2-A Layer 3 (#179): call subgen via subarr proxy. ~6-12s typical
  // on a warm model. On 503 (capability missing) surface the upgrade
  // hint inline so the user knows their subgen is too old, rather than
  // making them dig in logs.
  const runWhisper = async () => {
    setWhisperRunning(true);
    setWhisperError(null);
    setWhisperResult(null);
    try {
      const r = await fetch('/api/audio-lang/whisper-detect', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          canonical_path: row._canonical_path,
          chunks: 3,
          chunk_length_s: 30,
        }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${r.status}`);
      }
      const body = await r.json();
      setWhisperResult(body);
      // Auto-prefill the language selector with the aggregate top
      // result so a confident detection becomes one click to confirm.
      const agg = body && body.aggregate;
      if (agg && agg.language && agg.language !== 'und' && agg.n_total > 0) {
        // Match the audio_lang_store selector format. The selector
        // accepts ISO-639-1 (2-char) values; subgen returns 2-char.
        setSelected(agg.language);
      }
    } catch (e) {
      setWhisperError(String(e.message || e));
    } finally {
      setWhisperRunning(false);
    }
  };
  const save = async (langCode, opts = {}) => {
    const { source = 'user', confidence = 1.0, evidence } = opts;
    setSaving(true);
    setError(null);
    try {
      const r = await fetch('/api/audio-lang/verifications', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          canonical_path: row._canonical_path,
          lang_code: langCode,
          source,
          confidence,
          evidence: evidence || { notes: row.audio_label_notes || [], track },
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      // v1.1-O fix #193/#197: dispatch detail with file path so listeners
      // can do OPTIMISTIC local row updates (chip turns green immediately)
      // rather than waiting for the next coverage poll.
      window.dispatchEvent(new CustomEvent('audio-lang-verified', {
        detail: { file_canonical_path: row._canonical_path, lang_code: langCode },
      }));
      close();
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setSaving(false);
    }
  };

  const fmtT = (s) => {
    if (s == null) return '—';
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${String(sec).padStart(2, '0')}`;
  };
  const sampleUrl = posData && posData.positions && posData.positions[activeSampleIdx] != null
    ? `/api/audio-lang/sample?canonical_path=${encodeURIComponent(row._canonical_path)}&start=${posData.positions[activeSampleIdx]}&duration=12&track=${track}`
    : null;
  return (
    <div onClick={close} style={{
      position: 'fixed', inset: 0,
      background: 'rgba(0,0,0,0.55)', zIndex: 100,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <div onClick={(e) => e.stopPropagation()} className="panel" style={{
        width: 560, maxWidth: '92vw',
        padding: 20,
        display: 'flex', flexDirection: 'column', gap: 14,
      }}>
        <div>
          <div style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)', letterSpacing: '0.04em', textTransform: 'uppercase' }}>
            Audio language review
          </div>
          <div style={{ fontSize: 18, fontWeight: 600, color: 'var(--fg-0)', marginTop: 4 }}>
            {row.title}{row.ep ? ` · ${row.ep}` : ''}
          </div>
          <div className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)', marginTop: 4, wordBreak: 'break-all' }}>
            {row._canonical_path}
          </div>
        </div>

        <div style={{ background: 'var(--bg-1)', borderRadius: 'var(--radius-md)', padding: 10, fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
          <div style={{ marginBottom: 4 }}><b>Evidence trail:</b></div>
          {(row.audio_label_notes && row.audio_label_notes.length)
            ? <ul style={{ margin: 0, paddingLeft: 18 }}>
                {row.audio_label_notes.map((n, i) => <li key={i}>{n}</li>)}
              </ul>
            : <span style={{ color: 'var(--fg-3)' }}>No evidence captured.</span>}
          <div style={{ marginTop: 6, fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>
            ffprobe: {row.audio} · originalLanguage: {row.original_language || '—'}
          </div>
        </div>

        {/* v1.1-O Layer 4++: audio player. Three sample positions picked
            from non-silent regions; click button to swap. Audio element
            re-creates when src changes so the new sample auto-loads. */}
        <div style={{ background: 'var(--bg-1)', borderRadius: 'var(--radius-md)', padding: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)', fontWeight: 600 }}>🎧 Listen to the audio</span>
            {posData && posData.audio_tracks > 1 && (
              <>
                <span style={{ color: 'var(--bg-5)' }}>·</span>
                <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>track</span>
                {Array.from({ length: posData.audio_tracks }).map((_, i) => (
                  <button key={i} onClick={() => setTrack(i)}
                    className={`chip ${track === i ? 'violet' : ''}`}
                    title={`Switch to audio track ${i}`}
                    style={{ height: 22, padding: '0 8px', fontSize: 'var(--text-2xs)', cursor: 'pointer' }}>
                    {i}
                  </button>
                ))}
              </>
            )}
            <span style={{ flex: 1 }} />
            {posData && posData.method === 'vad' && (
              <span title="Clips picked by detecting actual speech (silero VAD), not just non-silence"
                style={{ fontSize: 'var(--text-2xs)', color: 'var(--violet)', whiteSpace: 'nowrap', marginRight: 6 }}>
                🎙 speech-detected
              </span>
            )}
            {posData && (
              <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>
                runtime {fmtT(posData.duration_s)}
              </span>
            )}
          </div>

          {posLoading && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 0' }}>
              <span className="spinner-ring" />
              <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
                Analyzing audio for speech… (one-time per file)
              </span>
            </div>
          )}

          {/* #9: graceful degrade when subarr's view of the path
              doesn't resolve to a real file (path-prefix mismatch
              between subarr and Sonarr/Plex is the common cause).
              The user can still confirm the language; the audio
              preview is a nice-to-have. */}
          {!posLoading && posData?.unavailable && (
            <div style={{
              display: 'flex', alignItems: 'flex-start', gap: 10, padding: '10px 12px',
              background: 'rgba(245, 158, 11, 0.10)',
              border: '1px solid rgba(245, 158, 11, 0.25)',
              borderRadius: 'var(--radius-md)',
              fontSize: 'var(--text-xs)', color: 'var(--fg-2)',
            }}>
              <span style={{ fontSize: 14 }}>⚠</span>
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 4 }}>
                <span style={{ color: 'var(--fg-1)', fontWeight: 600 }}>
                  Audio preview unavailable
                </span>
                <span>
                  Subarr can't read this file from disk. You can still confirm
                  the language below. Common cause: subarr's media mount
                  doesn't match where Sonarr/Plex sees the file. Check
                  ARR_PATH_PREFIX or the library's *arr prefix in
                  Settings → Libraries.
                </span>
                {posData.reason && (
                  <span className="mono" style={{ color: 'var(--fg-3)', fontSize: 'var(--text-2xs)' }}>
                    {posData.reason}
                  </span>
                )}
              </div>
            </div>
          )}

          {posData && posData.positions && posData.positions.length > 0 && (
            <>
              <div style={{ display: 'flex', gap: 6 }}>
                {posData.positions.map((p, i) => (
                  <button key={i} onClick={() => setActiveSampleIdx(i)}
                    className={`chip ${activeSampleIdx === i ? 'violet' : ''}`}
                    title={`Sample ${i + 1}: 5 seconds starting at ${fmtT(p)}`}
                    style={{ flex: 1, justifyContent: 'center', cursor: 'pointer', padding: '4px 8px' }}>
                    Sample {i + 1} · {fmtT(p)}
                  </button>
                ))}
              </div>
              {sampleUrl && (
                <audio key={sampleUrl} controls autoPlay
                  src={sampleUrl}
                  style={{ width: '100%', height: 36 }}>
                  Your browser doesn&apos;t support HTML5 audio.
                </audio>
              )}
              <div style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>
                Samples picked from dialog-dense regions (silence avoided). Switch sample or track to
                hear different sections, then assign the language below.
              </div>
            </>
          )}

          {posData && (!posData.positions || posData.positions.length === 0) && (
            <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
              Couldn&apos;t find a non-silent region in this file. (Very short / all-music / corrupted?)
            </div>
          )}
        </div>

        {/* v1.2-A Layer 3 (#179): robust Whisper detection. The user
            clicks → subgen runs N-chunk detection (~6-12s) → we render
            the per-chunk breakdown + aggregate vote and auto-prefill
            the language selector below. Calibrated confidence: 3/3 high
            prob = trust; 2/3 with mid prob = surface the disagreement. */}
        <div data-testid="detection-evidence" style={{ background: 'var(--bg-1)', borderRadius: 'var(--radius-md)', padding: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)', fontWeight: 600 }}>
              🤖 Ask Whisper (Layer 3)
            </span>
            <span style={{ flex: 1 }} />
            <button className="btn ghost sm"
              onClick={runWhisper}
              disabled={whisperRunning}
              title="Sample 3 audio chunks across the file and ask Whisper to identify the language. ~6-12s on a warm GPU."
              aria-label="Run Whisper multi-chunk language detection">
              {whisperRunning ? '…running' : (whisperResult ? '↻ re-run' : 'run detection')}
            </button>
          </div>
          {whisperRunning && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 0' }}>
              <span className="spinner-ring" />
              <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
                Sampling 3 chunks · running Whisper inference… (~6-12s)
              </span>
            </div>
          )}
          {whisperError && (
            <div style={{ fontSize: 'var(--text-xs)', color: 'var(--error-500)' }}>
              {whisperError}
            </div>
          )}
          {whisperResult && !whisperRunning && (() => {
            const agg = whisperResult.aggregate || {};
            const chunks = whisperResult.chunks || [];
            const confident = agg.n_total > 0 && agg.n_agreeing === agg.n_total
                              && (agg.min_probability || 0) >= 0.85;
            const suspect = !confident && agg.n_total > 0;
            return (
              <>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <span className={`chip ${confident ? 'violet' : (suspect ? 'amber' : '')}`}
                    style={{ height: 22, padding: '0 8px', fontSize: 'var(--text-2xs)' }}>
                    {confident ? '✓ confident' : (suspect ? '⚠ suspect' : '? no data')}
                  </span>
                  <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-1)' }}>
                    <b className="mono">{agg.language || 'und'}</b>
                    {agg.n_total > 0 && (
                      <span style={{ color: 'var(--fg-3)' }}>
                        {' '}· {agg.n_agreeing}/{agg.n_total} agree
                        {' '}· min p={(agg.min_probability || 0).toFixed(2)}
                      </span>
                    )}
                  </span>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {chunks.map((c, i) => (
                    <div key={i} className="mono" style={{
                      display: 'flex', gap: 10,
                      fontSize: 'var(--text-2xs)',
                      color: c.error ? 'var(--error-500)' : 'var(--fg-2)',
                    }}>
                      <span style={{ color: 'var(--fg-3)', minWidth: 70 }}>
                        chunk {i + 1} · {Math.floor((c.offset_s || 0) / 60)}:
                        {String(Math.floor((c.offset_s || 0) % 60)).padStart(2, '0')}
                      </span>
                      {c.error ? (
                        <span>{c.error}</span>
                      ) : (
                        <>
                          <span style={{ color: 'var(--fg-1)', minWidth: 60 }}>{c.language}</span>
                          <span style={{ color: 'var(--fg-3)' }}>p={(c.probability || 0).toFixed(2)}</span>
                          {c.language_name && (
                            <span style={{ color: 'var(--fg-3)' }}>· {c.language_name}</span>
                          )}
                        </>
                      )}
                    </div>
                  ))}
                </div>
                <div style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>
                  {confident
                    ? 'All chunks agree with high probability — prefilled below.'
                    : (suspect
                        ? 'Chunks disagree or low probability — listen above and confirm manually.'
                        : 'Whisper returned no signal — listen above and pick from the list.')}
                </div>
                {/* #90 (B): accept the machine detection AS whisper-verified
                    (distinct from Confirm below, which stores it as YOUR call).
                    Stores source=whisper → cyan badge + tag-mismatch flag. */}
                {agg.language && agg.language !== 'und' && agg.n_total > 0 && (
                  <button className="btn sm"
                    onClick={() => save(agg.language, {
                      source: 'whisper',
                      confidence: agg.n_total ? +(agg.n_agreeing / agg.n_total).toFixed(2) : 0,
                      evidence: whisperResult,
                    })}
                    disabled={saving}
                    title="Store this as the Whisper-verified audio language (machine detection). Renders the Whisper badge and flags a tag mismatch — distinct from Confirm, which records it as your own verification."
                    style={{ alignSelf: 'flex-start', background: 'rgba(34,211,238,0.18)', color: '#22d3ee', border: '1px solid rgba(34,211,238,0.35)' }}>
                    {saving ? 'Saving…' : `✓ Accept as Whisper-verified (${agg.language})`}
                  </button>
                )}
              </>
            );
          })()}
          {!whisperResult && !whisperRunning && !whisperError && (
            <div style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>
              Multi-chunk Whisper detection samples three points across the file (skipping
              opening + closing credits) and votes on the language. Useful when ffprobe is
              tagged wrong or unknown. Requires subarr-subgen v4.5+.
            </div>
          )}
        </div>

        <div>
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)', marginBottom: 6 }}>
            Set the actual audio language{posData && posData.audio_tracks > 1 ? ` for track ${track}` : ''}:
          </div>
          <select value={selected} onChange={(e) => setSelected(e.target.value)}
            style={{ width: '100%', padding: '8px 10px', background: 'var(--bg-1)',
                     color: 'var(--fg-0)', border: 'var(--border)', borderRadius: 'var(--radius-md)' }}>
            {LANG_PICKS.map(([c, n]) => <option key={c} value={c}>{n} ({c})</option>)}
          </select>
        </div>

        {error && <div style={{ color: 'var(--error-500)', fontSize: 'var(--text-xs)' }}>{error}</div>}

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, alignItems: 'center' }}>
          {saving && (
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)', marginRight: 'auto' }}>
              💾 Saving verification…
            </span>
          )}
          <button className="btn ghost" onClick={close} disabled={saving}>cancel</button>
          <button data-testid="review-confirm" className="btn" onClick={() => save(selected)} disabled={saving}
            style={{ background: 'var(--violet-500)', color: '#fff' }}>
            {saving ? 'Saving…' : `Confirm ${selected}`}
          </button>
        </div>
      </div>
    </div>
  );
}

function CoverageHeader({ allSelected }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10,
      padding: '0 12px',
      height: 32,
      background: 'var(--bg-1)',
      borderBottom: 'var(--border)',
      position: 'sticky', top: 0, zIndex: 2,
    }}>
      <div style={{ width: COL.check, flex: `0 0 ${COL.check}px` }}>
        <CheckBox checked={allSelected} indeterminate />
      </div>
      <HeaderCell w={COL.score} right
        tip="Priority score (0-10). Higher = subarr thinks this should be queued first. Factors: NOW PLAYING in Plex, just imported, watched recently, monitored, foreign-language original. Click reasons in row to expand.">score</HeaderCell>
      <HeaderCell w={COL.type}
        tip="Media type — episode (tv) or movie (mov)." />
      <HeaderCell
        tip="Series or movie title.">title</HeaderCell>
      <HeaderCell w={COL.ep}
        tip="Episode reference (S01E03) — blank for movies.">episode</HeaderCell>
      <HeaderCell w={COL.langs}
        tip="Subtitle languages BAZARR IS MISSING for this file — not languages already present. ISO 639-1 codes (en, es, fr, de…).">wanted</HeaderCell>
      <HeaderCell w={COL.orig}
        tip="Original language of the show/movie per Sonarr/Radarr (TVDB/TMDB metadata). Helps you spot foreign-original content at a glance.">orig</HeaderCell>
      <HeaderCell w={COL.mon} center
        tip="Monitored by Sonarr/Radarr. Filled = monitored, empty = not monitored.">mon</HeaderCell>
      <HeaderCell w={COL.audio}
        tip="Audio track languages in the file. From ffprobe stream tags, stream titles, or user verification. 'und' = no language metadata at all (encoder didn't tag the stream).">audio</HeaderCell>
      <HeaderCell w={COL.reason}
        tip="Why this row is in coverage: bazarr-wanted (Bazarr's gap list), no-track (no audio/sub data), embedded-only (only embedded sub), low-score (low priority), unmonitored.">reason</HeaderCell>
      <HeaderCell w={COL.action} />
    </div>
  );
}

function CheckBox({ checked, indeterminate }) {
  const filled = checked || indeterminate;
  return (
    <span style={{
      display: 'inline-flex', width: 14, height: 14,
      border: `1px solid ${filled ? 'var(--violet-500)' : 'var(--bg-5)'}`,
      borderRadius: 3,
      background: filled ? 'var(--violet-500)' : 'transparent',
      alignItems: 'center', justifyContent: 'center',
      color: '#fff', fontSize: 10, lineHeight: 1,
    }}>
      {checked ? '✓' : indeterminate ? <span style={{ width: 6, height: 1.5, background: '#fff' }} /> : ''}
    </span>
  );
}

// #7 perf: memoized so unrelated CoveragePage re-renders (pending-review
// poll, queue/probe state) don't re-render every row. Relies on STABLE
// props: onClick=toggleRow + onQueue=handleRowQueue (both useCallback),
// and r identity stable across those re-renders (the rows memo only
// rebuilds on data/selection change). The row passes r.id to onClick so
// the parent's handler stays referentially stable (the documented trap:
// an inline `() => toggleRow(r.id)` per row would defeat the memo).
function CoverageRowImpl({ r, onClick, onQueue, queuing }) {
  return (
    <div className="cov-row"
      data-testid="coverage-row"
      data-reason={r.reason}
      data-vstate={r.vstate}
      style={{
      display: 'flex', alignItems: 'center', gap: 10,
      padding: '0 12px',
      height: 34,
      borderBottom: '1px solid var(--bg-3)',
      background: r.sel ? 'rgba(139,92,246,0.05)' : 'transparent',
      cursor: 'pointer',
      transition: 'background var(--dur-fast) var(--ease-out)',
    }}
    onClick={() => onClick && onClick(r.id)}>
      <div style={{ width: COL.check, flex: `0 0 ${COL.check}px` }}>
        <CheckBox checked={r.sel} />
      </div>
      <div className="num mono" title={`Priority score: ${r.score.toFixed(1)} / 10. Higher = subarr thinks this should be queued sooner.`} style={{
        width: COL.score, flex: `0 0 ${COL.score}px`,
        textAlign: 'right',
        fontSize: 'var(--text-base)',
        fontWeight: 600,
        color: scoreColor(r.score),
        cursor: 'help',
      }}>{r.score.toFixed(1)}</div>
      <div style={{ width: COL.type, flex: `0 0 ${COL.type}px`, textAlign: 'center' }}>
        <TypeGlyph t={r.type} />
      </div>
      <div style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{
          fontSize: 'var(--text-base)',
          color: 'var(--fg-0)',
          fontWeight: 500,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          minWidth: 0,
        }}>{r.title}</span>
        <ScoringBadges r={r} />
        <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)', flex: '0 0 auto' }}>· {r.size}</span>
      </div>
      <div className="mono num" style={{ width: COL.ep, flex: `0 0 ${COL.ep}px`, fontSize: 'var(--text-xs)', color: 'var(--fg-1)' }}>
        {r.ep || '—'}
      </div>
      <div title={`Bazarr is missing these subtitle languages: ${(r.langs || []).join(', ') || '—'}`}
           style={{ width: COL.langs, flex: `0 0 ${COL.langs}px`, cursor: 'help' }}>
        <LangChips langs={r.langs} />
      </div>
      <div title={r.orig_lang_name ? `Original language per Sonarr/Radarr: ${r.orig_lang_name}` : 'No original-language metadata'}
           style={{ width: COL.orig, flex: `0 0 ${COL.orig}px`, cursor: 'help', display: 'flex', alignItems: 'center' }}>
        {r.orig_lang ? (
          <span className="mono" style={{
            padding: '1px 6px', borderRadius: 3,
            background: 'var(--bg-3)',
            color: r.orig_lang === 'en' ? 'var(--fg-3)' : 'var(--fg-1)',
            fontSize: 'var(--text-2xs)',
            letterSpacing: '0.04em',
          }}>{r.orig_lang}</span>
        ) : <span style={{ color: 'var(--fg-3)', fontSize: 'var(--text-2xs)' }}>—</span>}
      </div>
      <div title={r.mon ? 'Monitored by Sonarr/Radarr' : 'NOT monitored'}
           style={{ width: COL.mon, flex: `0 0 ${COL.mon}px`, textAlign: 'center', cursor: 'help' }}>
        <YesNo on={r.mon} />
      </div>
      <div className="mono"
           title={r.audio === 'und'
             ? "'und' = no language metadata in the file (encoder skipped the tag). Mark for review via Audio Lang verification."
             : `Audio track languages detected: ${r.audio}`}
           style={{ width: COL.audio, flex: `0 0 ${COL.audio}px`, fontSize: 'var(--text-xs)', color: 'var(--fg-1)', cursor: 'help',
                    display: 'flex', alignItems: 'center', minWidth: 0 }}>
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'inline-flex', gap: 6 }}>
          {String(r.audio || '').split(',').filter(Boolean).map((l, i) => <LangTag key={l + i} value={l} size={11} />)}
        </span>
        <AudioLabelChip r={r} onClick={(row) => { window.dispatchEvent(new CustomEvent('open-audio-review', { detail: row })); }} />
      </div>
      <div style={{ width: COL.reason, flex: `0 0 ${COL.reason}px` }}>
        <ReasonChip r={r.reason} />
      </div>
      <div style={{ width: COL.action, flex: `0 0 ${COL.action}px`, textAlign: 'center', display: 'flex', gap: 4, justifyContent: 'center' }}>
        {r._sonarr_episode_id && (
          <button
            className="btn ghost"
            onClick={(e) => { e.stopPropagation(); window.dispatchEvent(new CustomEvent('open-arbiter', { detail: r })); }}
            title="Check Bazarr first — see if a human-translated sub is available before queueing Whisper. Saves GPU on backlog."
            style={{ height: 22, padding: '0 6px', fontSize: 'var(--text-2xs)' }}>
            ⌕
          </button>
        )}
        <button
          data-testid="coverage-row-queue"
          className="btn ghost"
          onClick={(e) => { e.stopPropagation(); onQueue && onQueue(r); }}
          disabled={queuing}
          title="Queue this row for subgen / Whisper transcription"
          style={{ height: 22, padding: '0 8px', fontSize: 'var(--text-2xs)' }}>
          {queuing ? '…' : '↻'}
        </button>
      </div>
    </div>
  );
}

// ─── Selection action bar ────────────────────────────────────────
const CoverageRow = React.memo(CoverageRowImpl);


function SelectionBar({ n, reasonFilter, onClear, onQueue, queueState }) {
  if (!n) return null;
  const queuing = queueState?.busy;
  const queueLabel = queuing
    ? `Queueing ${queueState.done}/${queueState.total}…`
    : `Queue selected (${n})`;
  return (
    <div style={{
      position: 'sticky', bottom: 0,
      display: 'flex', alignItems: 'center', gap: 12,
      padding: 'var(--row-cozy)',
      background: 'var(--bg-2)',
      border: 'var(--border)',
      borderRadius: 'var(--radius-lg)',
      boxShadow: '0 -2px 12px rgba(0,0,0,0.25)',
    }}>
      <CheckBox checked />
      <span style={{ fontSize: 'var(--text-md)', fontWeight: 600 }}>{n} selected</span>
      <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>· filter <span className="mono">{reasonFilter}</span></span>
      {queueState?.errors > 0 && (
        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--error-500)' }}>
          · {queueState.errors} failed
        </span>
      )}
      <span style={{ flex: 1 }} />
      <button className="btn ghost" onClick={onClear} disabled={queuing}>Clear</button>
      <button className="btn primary" onClick={onQueue} disabled={queuing}>{queueLabel}</button>
    </div>
  );
}

async function queueRow(row) {
  const body = row._sonarr_episode_id
    ? { sonarr_episode_id: row._sonarr_episode_id }
    : { canonical_path: row._canonical_path };
  const r = await fetch('/api/coverage/queue', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok && r.status !== 202) {
    const text = await r.text().catch(() => '');
    throw new Error(`HTTP ${r.status}: ${text.slice(0, 200)}`);
  }
  return r.json().catch(() => ({}));
}

// ─── Tree-by-show grouping ───────────────────────────────────────
// Groups rows into Show > Season > Episode. Each level shows a rollup
// count on the right (matches original subarr's "9 eps wanted · 3 seasons"
// style). Show + Season toggle expanded state locally per group.
function buildShowTree(rows) {
  const shows = new Map();
  for (const r of rows) {
    if (r.type !== 'tv') continue;  // movies handled separately below
    if (!shows.has(r.title)) {
      shows.set(r.title, { title: r.title, seasons: new Map(), all: [] });
    }
    const show = shows.get(r.title);
    show.all.push(r);
    const seasonNum = (r.ep || '').match(/^S(\d{2})/)?.[1] || '?';
    if (!show.seasons.has(seasonNum)) {
      show.seasons.set(seasonNum, { num: seasonNum, eps: [] });
    }
    show.seasons.get(seasonNum).eps.push(r);
  }
  return shows;
}

function GroupHeader({ depth, label, onClick, expanded, allSelected, indeterminate, onToggleSelect, rightMeta }) {
  return (
    <div onClick={onClick} style={{
      display: 'flex', alignItems: 'center', gap: 10,
      padding: `0 16px 0 ${16 + depth * 22}px`,
      height: 34,
      borderBottom: '1px solid var(--bg-3)',
      background: 'var(--bg-1)',
      cursor: 'pointer',
    }}>
      <span onClick={(e) => { e.stopPropagation(); onToggleSelect && onToggleSelect(); }}
        style={{ cursor: onToggleSelect ? 'pointer' : 'default' }}>
        <CheckBox checked={allSelected} indeterminate={indeterminate && !allSelected} />
      </span>
      <span style={{ color: 'var(--fg-3)', fontSize: 'var(--text-xs)', width: 12 }}>
        {expanded ? '▾' : '▸'}
      </span>
      <span style={{
        fontSize: 'var(--text-sm)',
        fontWeight: depth === 0 ? 600 : 500,
        color: depth === 0 ? 'var(--fg-0)' : 'var(--fg-1)',
        flex: 1,
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>{label}</span>
      <span className="num mono" style={{
        fontSize: 'var(--text-xs)',
        color: 'var(--fg-3)',
      }}>{rightMeta}</span>
    </div>
  );
}

// #140: dismissable notice rendered under a show header when the series is
// flagged mixed-language. Lists the foreign langs + a per-episode breakdown,
// with a one-click dismiss for genuinely-multilingual shows.
function MixedSeriesNotice({ show, onDismiss }) {
  const flagged = show.all.find((r) => r.series_mixed);
  if (!flagged) return null;
  const langs = (flagged.series_mixed_langs || []).join(', ');
  const breakdown = show.all
    .filter((r) => r.detected_lang)
    .map((r) => `${r.ep || r.title}: ${r.detected_lang}`)
    .join(' · ');
  return (
    <div style={{
      margin: '2px 0 6px 44px', padding: '8px 12px',
      background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.35)',
      borderRadius: 'var(--radius-md)', display: 'flex', alignItems: 'center', gap: 12,
    }}>
      <span style={{ flex: 1, minWidth: 0, fontSize: 'var(--text-sm)', color: 'var(--fg-1)' }}>
        <b style={{ color: '#f87171' }}>⚠ Mixed languages — likely mis-grouped.</b>{' '}
        Episodes resolve to multiple foreign spoken languages (<b>{langs}</b>) — probably
        two different shows merged into one Sonarr series.
        {breakdown ? <span style={{ color: 'var(--fg-3)' }}> {' · '}{breakdown}</span> : null}
      </span>
      <button className="btn" style={{ fontSize: 'var(--text-xs)', flex: 'none' }}
        title="This is a genuinely multilingual show — stop flagging it"
        onClick={(e) => { e.stopPropagation(); onDismiss && onDismiss(flagged.series_path); }}>
        Dismiss
      </button>
    </div>
  );
}

function CoverageTree({ rows, selected, toggleRow, onQueue, rowQueuing, onDismissMixed }) {
  const tree = useMemo(() => buildShowTree(rows), [rows]);
  const movies = useMemo(() => rows.filter((r) => r.type === 'mov'), [rows]);
  const [expandedShows, setExpandedShows] = useState(() => new Set());
  const [expandedSeasons, setExpandedSeasons] = useState(() => new Set());

  const toggleShow = (title) => setExpandedShows((prev) => {
    const next = new Set(prev);
    if (next.has(title)) next.delete(title); else next.add(title);
    return next;
  });
  const toggleSeason = (key) => setExpandedSeasons((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });

  const out = [];
  for (const show of tree.values()) {
    const showExpanded = expandedShows.has(show.title);
    const epCount = show.all.length;
    const seasonCount = show.seasons.size;
    const allShowSelected = show.all.every((r) => selected.has(r.id));
    const anyShowSelected = show.all.some((r) => selected.has(r.id));
    out.push(
      <GroupHeader
        key={`show-${show.title}`}
        depth={0}
        label={show.title}
        onClick={() => toggleShow(show.title)}
        expanded={showExpanded}
        allSelected={allShowSelected}
        indeterminate={anyShowSelected}
        onToggleSelect={() => {
          if (allShowSelected) {
            for (const r of show.all) if (selected.has(r.id)) toggleRow(r.id);
          } else {
            for (const r of show.all) if (!selected.has(r.id)) toggleRow(r.id);
          }
        }}
        rightMeta={`${epCount} eps wanted · ${seasonCount} season${seasonCount === 1 ? '' : 's'}`}
      />
    );
    // #140: surface the mis-grouped warning at series level (shows even when
    // the series is collapsed — it's a data-integrity alert worth seeing).
    if (show.all.some((r) => r.series_mixed)) {
      out.push(
        <MixedSeriesNotice key={`mixed-${show.title}`} show={show} onDismiss={onDismissMixed} />
      );
    }
    if (!showExpanded) continue;
    const seasonKeys = Array.from(show.seasons.keys()).sort();
    for (const sk of seasonKeys) {
      const season = show.seasons.get(sk);
      const seasonId = `${show.title}|S${sk}`;
      const seasonExpanded = expandedSeasons.has(seasonId);
      const allSeasonSelected = season.eps.every((r) => selected.has(r.id));
      const anySeasonSelected = season.eps.some((r) => selected.has(r.id));
      out.push(
        <GroupHeader
          key={`season-${seasonId}`}
          depth={1}
          label={sk === '?' ? 'Season (unknown)' : `Season ${parseInt(sk, 10)}`}
          onClick={() => toggleSeason(seasonId)}
          expanded={seasonExpanded}
          allSelected={allSeasonSelected}
          indeterminate={anySeasonSelected}
          onToggleSelect={() => {
            if (allSeasonSelected) {
              for (const r of season.eps) if (selected.has(r.id)) toggleRow(r.id);
            } else {
              for (const r of season.eps) if (!selected.has(r.id)) toggleRow(r.id);
            }
          }}
          rightMeta={`${season.eps.length} ep${season.eps.length === 1 ? '' : 's'} wanted`}
        />
      );
      if (!seasonExpanded) continue;
      for (const r of season.eps) {
        out.push(
          <div key={`ep-${r.id}`} style={{ paddingLeft: 44 }}>
            <CoverageRow
              r={r}
              onClick={toggleRow}
              onQueue={onQueue}
              queuing={rowQueuing.has(r.id)}
            />
          </div>
        );
      }
    }
  }
  // Movies at the bottom, ungrouped (no concept of season).
  if (movies.length) {
    out.push(
      <GroupHeader
        key="movies-header"
        depth={0}
        label="Movies"
        onClick={() => {}}
        expanded={true}
        allSelected={movies.every((r) => selected.has(r.id))}
        indeterminate={movies.some((r) => selected.has(r.id))}
        rightMeta={`${movies.length} movie${movies.length === 1 ? '' : 's'} wanted`}
      />,
      ...movies.map((r) => (
        <div key={`mov-${r.id}`} style={{ paddingLeft: 22 }}>
          <CoverageRow
            r={r}
            onClick={toggleRow}
            onQueue={onQueue}
            queuing={rowQueuing.has(r.id)}
          />
        </div>
      ))
    );
  }
  return <>{out}</>;
}

// ─── Page ────────────────────────────────────────────────────────
// Probe-gate sticky buckets. Read-only: no checkbox, no queue button — a
// row only becomes actionable once subarr has actually probed it. Holds
// rows visibly until then (or, for failures, until the file is fixed) so
// nothing the user hasn't seen silently disappears.
function CoverageBucket({ kind, rows, onProbeNow, probing }) {
  const [open, setOpen] = useState(false);
  if (!rows.length) return null;
  const META = {
    unprobed: {
      icon: '⏳', label: 'Analyzing',
      tint: 'var(--cyan-500)',
      blurb: 'subarr probes each file before calling it a gap, so it never '
           + 'queues something already covered. These are awaiting analysis '
           + 'and will move up once probed (or drop out if already covered).',
    },
    probe_failed: {
      icon: '⚠', label: "Couldn't analyze",
      tint: 'var(--error-500)',
      blurb: "subarr couldn't probe these files (unreadable, corrupt, or the "
           + 'probe timed out). Held here, not silently dropped — fix the '
           + 'file or check logs, and they re-probe on the next walk.',
    },
    // #79: forced-only embedded-EN that the connected subgen will SKIP because
    // its IGNORE_FORCED_SUBTITLES is off. Distinct + visible (not a fillable
    // gap, not silently hidden) with the exact knob to flip to make it fillable.
    forced_skip: {
      icon: '⏭', label: 'subgen will skip (forced-only)',
      tint: 'var(--warn-500)',
      blurb: 'These files have only a FORCED English embedded subtitle (covers '
           + 'foreign dialogue only, not a full transcript). Your connected '
           + 'subgen has IGNORE_FORCED_SUBTITLES off, so it would SKIP them '
           + 'rather than transcribe a full sub — they are not actionable gaps. '
           + 'Set IGNORE_FORCED_SUBTITLES=true on subgen to have it fill these.',
    },
  };
  const meta = META[kind] || META.probe_failed;
  return (
    <div className="panel" style={{
      flexShrink: 0, padding: '10px 14px',
      borderLeft: `2px solid ${meta.tint}`,
    }}>
      <div onClick={() => setOpen(o => !o)}
           style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
        <span>{meta.icon}</span>
        <span style={{ fontWeight: 600, color: 'var(--fg-0)' }}>{meta.label}</span>
        <span className="chip">{rows.length}</span>
        <span style={{ flex: 1 }} />
        {kind === 'unprobed' && onProbeNow && (
          <button
            className="btn btn-sm"
            disabled={probing}
            onClick={(e) => { e.stopPropagation(); onProbeNow(); }}
            title="Probe these files now so they become verified gaps (or drop out if already covered). Probing also runs automatically on each refresh."
            style={{ marginRight: 4 }}
          >
            {probing ? 'Probing…' : 'Probe now'}
          </button>
        )}
        <span style={{ color: 'var(--fg-3)', fontSize: 'var(--text-xs)' }}>
          {open ? 'hide' : 'show'}
        </span>
      </div>
      <div style={{ color: 'var(--fg-2)', fontSize: 'var(--text-xs)', marginTop: 4 }}>
        {meta.blurb}
      </div>
      {open && (
        <div style={{ marginTop: 8, maxHeight: '55vh', overflow: 'auto' }}>
          {rows.map(r => (
            <div key={r.id} style={{
              display: 'flex', gap: 10, padding: '4px 0',
              fontSize: 'var(--text-sm)', borderTop: '1px solid var(--bg-3)',
            }}>
              <span style={{ color: 'var(--fg-1)' }}>{r.title}</span>
              {r.ep && <span className="mono" style={{ color: 'var(--fg-3)' }}>{r.ep}</span>}
              <span style={{ flex: 1 }} />
              <span className="mono" style={{ color: 'var(--fg-3)', fontSize: 'var(--text-2xs)' }}>
                {r.type}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}


export function CoveragePage() {
  const [groupBy, setGroupBy] = useState('tree');  // tree-by-show default — matches original subarr
  const [reasonFilter, setReasonFilter] = useState('all');
  const [typeFilter, setTypeFilter] = useState('all');
  const [monitoredOnly, setMonitoredOnly] = useState(true);
  const [selected, setSelected] = useState(() => new Set());
  const [rowQueuing, setRowQueuing] = useState(() => new Set()); // ids in flight
  const [queueState, setQueueState] = useState({ busy: false, done: 0, total: 0, errors: 0 });
  const [walking, setWalking] = useState(false);

  // Pending audio-language review count surfaced in the header tile + welcome
  // card. PendingReviewBanner already polls /api/audio-lang/pending-review,
  // but it does so inside its own component scope — duplicate the small
  // fetch here so the header tiles update without prop-drilling.
  const [pendingReviewCount, setPendingReviewCount] = useState(0);
  useEffect(() => {
    let cancelled = false;
    const fetchCount = () => fetch('/api/audio-lang/pending-review', { credentials: 'same-origin' })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (!cancelled) setPendingReviewCount(d?.count || 0); })
      .catch(() => {});
    fetchCount();
    const handler = () => fetchCount();
    window.addEventListener('audio-lang-verified', handler);
    return () => { cancelled = true; window.removeEventListener('audio-lang-verified', handler); };
  }, []);

  const { data, loading, error, refetch } = useLiveCoverage();

  // #140: dismiss a mis-grouped-series flag, then refetch so the notice clears.
  const dismissMixed = useCallback(async (seriesPath) => {
    if (!seriesPath) return;
    try {
      await fetch('/api/audio-lang/mixed-dismiss', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ series_path: seriesPath }),
      });
      refetch();
    } catch { /* best-effort — the flag reappears next walk if this failed */ }
  }, [refetch]);

  // Deep-link from chrome rail: /coverage#review auto-opens BatchReviewModal.
  useEffect(() => {
    if ((window.location.hash || '').toLowerCase() === '#review') {
      window.dispatchEvent(new CustomEvent('open-batch-review'));
    }
  }, []);

  // Normalize once per payload.
  const allRows = useMemo(() => {
    if (!data?.items) return null;
    return data.items.map((it, idx) => normalizeRow(it, idx, data.settle_minutes || 0));
  }, [data]);

  // Apply UI filters. Probe-gate: the gap table is VERIFIED-only — an
  // un-probed row isn't a trustworthy gap (it may already have an embedded
  // sub subgen would skip), so it never enters the table or bulk-select.
  const rows = useMemo(() => {
    if (!allRows) return [];
    return allRows.filter(r => {
      if (r.vstate !== 'verified') return false;
      // #79: forced-only EN that subgen will skip is non-actionable — it lives
      // in its own bucket below, not the gap table.
      if (r.forced_skip) return false;
      if (monitoredOnly && !r.mon) return false;
      if (reasonFilter !== 'all' && r.reason !== reasonFilter) return false;
      if (typeFilter !== 'all' && r.type !== typeFilter) return false;
      return true;
    }).map(r => ({ ...r, sel: selected.has(r.id) }));
  }, [allRows, monitoredOnly, reasonFilter, typeFilter, selected]);

  // Probe-gate buckets — sticky (NOT subject to the UI filters above) and
  // never queueable. They hold rows until the probe runs, then those rows
  // become verified (entering the table) or drop out as covered.
  const analyzingRows = useMemo(
    () => (allRows || []).filter(r => r.vstate === 'unprobed'),
    [allRows],
  );
  const failedRows = useMemo(
    // #96/#62: 'unsupported' (disc images / multi-ep .iso) join 'probe_failed'
    // in the "Couldn't analyze" bucket — disqualified from gaps AND out of
    // "Analyzing", but still visible (no silent hole) and clearly non-actionable.
    () => (allRows || []).filter(r => r.vstate === 'probe_failed' || r.vstate === 'unsupported'),
    [allRows],
  );
  // #79: forced-only-EN rows the connected subgen will SKIP (IGNORE_FORCED_-
  // SUBTITLES off). Distinct, visible, non-actionable bucket — never presented
  // as a fillable gap, never silently hidden. Only verified rows qualify.
  const forcedSkipRows = useMemo(
    () => (allRows || []).filter(r => r.forced_skip && r.vstate === 'verified'),
    [allRows],
  );

  const toggleRow = useCallback((id) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

  const clearSelection = useCallback(() => setSelected(new Set()), []);

  const handleRowQueue = useCallback(async (row) => {
    setRowQueuing(prev => { const n = new Set(prev); n.add(row.id); return n; });
    try {
      await queueRow(row);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('queue row failed:', e);
      alert(`Queue failed: ${e.message}`);
    } finally {
      setRowQueuing(prev => { const n = new Set(prev); n.delete(row.id); return n; });
      refetch({ fresh: true, silent: true });
    }
  }, [refetch]);

  const handleBulkQueue = useCallback(async () => {
    const targets = rows.filter(r => selected.has(r.id));
    if (!targets.length) return;
    setQueueState({ busy: true, done: 0, total: targets.length, errors: 0 });
    let done = 0, errors = 0;
    // Serial — gives the backend room to breathe + Bazarr/Sonarr are
    // rate-limited upstream. Parallel-3 could come later if needed.
    for (const row of targets) {
      try { await queueRow(row); }
      catch (e) {
        errors += 1;
        // eslint-disable-next-line no-console
        console.error('bulk queue failed for', row.id, e);
      }
      done += 1;
      setQueueState({ busy: true, done, total: targets.length, errors });
    }
    setQueueState({ busy: false, done, total: targets.length, errors });
    setSelected(new Set());
    refetch({ fresh: true, silent: true });
  }, [rows, selected, refetch]);

  const handleRewalk = useCallback(async () => {
    setWalking(true);
    try {
      const r = await fetch('/api/schedule/coverage_walk/run-now', {
        method: 'POST',
        credentials: 'same-origin',
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await refetch({ fresh: true });
    } catch (e) {
      alert(`Re-walk failed: ${e.message}`);
    } finally {
      setWalking(false);
    }
  }, [refetch]);

  // "Probe now" on the Analyzing bucket — triggers a background coverage
  // refresh, which eager-probes the unprobed wanted files (PR-C). Probing
  // backgrounds; rows flip from Analyzing → verified gap (or drop out if
  // already covered) as they're probed. Poll the refresh status, then
  // refetch so the buckets update without a manual reload.
  const [probing, setProbing] = useState(false);
  const handleProbeNow = useCallback(async () => {
    setProbing(true);
    try {
      const r = await fetch('/api/coverage/refresh', {
        method: 'POST',
        credentials: 'same-origin',
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      // Wait for the rebuild (which kicks the eager-probe) to finish, then
      // refetch so newly-probed rows surface. Capped poll so a stuck build
      // can't pin the spinner forever.
      for (let i = 0; i < 90; i++) {
        await new Promise(res => setTimeout(res, 2000));
        try {
          const s = await fetch('/api/coverage/status', { credentials: 'same-origin' })
            .then(x => (x.ok ? x.json() : null));
          if (s && s.refreshing === false) break;
        } catch { /* transient — keep polling */ }
      }
      await refetch({ fresh: false, silent: true });
    } catch (e) {
      alert(`Probe failed: ${e.message}`);
    } finally {
      setProbing(false);
    }
  }, [refetch]);

  // Sync to Bazarr — fires Bazarr's scan-disk task directly. Useful when
  // user has just added/restored .srt files manually and wants Bazarr's
  // wanted count to drop NOW without waiting for the next coverage walk
  // (which auto-pokes Bazarr already, but is rate-limited to once per 5
  // minutes and only fires when stale-disk items are surfaced).
  const [bazarrSyncing, setBazarrSyncing] = useState(false);
  const handleBazarrSync = useCallback(async () => {
    setBazarrSyncing(true);
    try {
      const r = await fetch('/api/bazarr/sync-disk', {
        method: 'POST',
        credentials: 'same-origin',
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      // Re-fetch coverage shortly after so user sees Bazarr updates land.
      // Bazarr's scan-disk task is async on their side; give it 10s
      // before re-reading the wanted count.
      setTimeout(() => refetch({ fresh: true, silent: true }), 10000);
    } catch (e) {
      alert(`Bazarr sync failed: ${e.message}`);
    } finally {
      setBazarrSyncing(false);
    }
  }, [refetch]);

  const handleExportCsv = useCallback(() => {
    if (!rows.length) return;
    const headers = ['score', 'type', 'title', 'episode', 'reason', 'monitored', 'has_sub_on_disk', 'embedded', 'audio', 'canonical_path'];
    const csv = [headers.join(',')];
    for (const r of rows) {
      csv.push([
        r.score.toFixed(2), r.type,
        JSON.stringify(r.title), JSON.stringify(r.ep),
        r.reason, r.mon ? 1 : 0, r.disk ? 1 : 0, r.emb ? 1 : 0,
        JSON.stringify(r.audio), JSON.stringify(r._canonical_path || ''),
      ].join(','));
    }
    const blob = new Blob([csv.join('\n')], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `coverage-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }, [rows]);

  const selectedCount = rows.filter(r => r.sel).length;
  const isInitialLoad = loading && !data;
  const isError = error && !data;
  const isEmpty = !isInitialLoad && !isError && rows.length === 0;

  return (
    <main className="main-canvas" style={{ padding: '22px 24px 0', gap: 14 }}>
      {/* Page header */}
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 'var(--text-h1)', lineHeight: 'var(--lh-h1)', fontWeight: 600, letterSpacing: '-0.005em' }}>Coverage</h1>
          <div style={{ marginTop: 4, fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>
            Files where subarr thinks a subtitle is missing or below threshold.
          </div>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <button className="btn" onClick={handleExportCsv} disabled={!rows.length}>Export CSV</button>
          <button className="btn" onClick={handleBazarrSync} disabled={bazarrSyncing}
            title="Tell Bazarr to scan disk for .srt files it might have missed. Use after adding subs manually, or to clear stale-disk rows immediately.">
            {bazarrSyncing ? 'Syncing…' : 'Sync to Bazarr'}
          </button>
          <button className="btn" onClick={handleRewalk} disabled={walking}>
            {walking ? 'Walking…' : 'Re-walk now'}
          </button>
        </div>
      </div>

      {/* Friendly header — 4 status tiles + welcome card with quick actions.
          Mirrors the Home dashboard + Rules pages so the user lands on the
          same explanatory pattern wherever they go. */}
      <CoverageStatusRow data={data} rows={rows} pendingReview={pendingReviewCount} />
      <CoverageWelcomeCard rows={rows} pendingReview={pendingReviewCount} />

      {/* Coverage strip — kept below the panels as a one-line technical
          summary; the panels above are for "what's the situation?" and this
          is for "where does the data come from?" */}
      <div className="panel" style={{ padding: 'var(--row-cozy)' }}>
        <CoverageStrip data={data} loading={loading} error={error} />
      </div>

      {/* v1.1-O Layer 4: pending-review banner */}
      <PendingReviewBanner />

      {/* v1.1-O Layer 4: per-row audio review modal */}
      <AudioReviewModal />

      {/* v1.1-F Whisper-or-Bazarr arbiter modal */}
      <ArbiterModal />

      {/* v1.1-O Layer 4++: batch review modal */}
      <BatchReviewModal />

      {/* How-this-list-is-built explainer */}
      <details style={{
        background: 'var(--bg-1)', border: 'var(--border)',
        borderRadius: 'var(--radius-md)', padding: '8px 12px',
        fontSize: 'var(--text-xs)', color: 'var(--fg-2)',
      }}>
        <summary style={{ cursor: 'pointer', color: 'var(--fg-1)', userSelect: 'none' }}>
          How is this list built?
        </summary>
        <div style={{ marginTop: 8, lineHeight: 1.5 }}>
          <b>Bazarr</b> seeds the gap list (anything in <i>Wanted</i>).
          We reconcile each row against:
          <ul style={{ margin: '6px 0 6px 18px', padding: 0 }}>
            <li><b>Sonarr / Radarr</b> — episode/movie metadata + original-language hints.</li>
            <li><b>subarr ffprobe</b> — detects embedded English subs and audio tracks Bazarr can't see.</li>
            <li><b>Disk walk</b> — finds sidecar <code>.srt</code> files Bazarr hasn't picked up yet (we auto-poke its scan-disk task).</li>
            <li><b>Tautulli</b> — bumps the priority score for items watched in the last 7&nbsp;days.</li>
            <li><b>Ollama</b> — enriches release-name parsing (HI / SDH / forced flags) so we don't queue dupes.</li>
          </ul>
          Rows are suppressed when an embedded English track exists, a sibling <code>.srt</code>
          is already on disk, or the file's audio is already English. Toggle the filters above to see them.
        </div>
      </details>

      {/* Filter bar */}
      <FilterBar
        groupBy={groupBy} setGroupBy={setGroupBy}
        filtered={rows.length}
        reasonFilter={reasonFilter} setReasonFilter={setReasonFilter}
        typeFilter={typeFilter} setTypeFilter={setTypeFilter}
        monitoredOnly={monitoredOnly} setMonitoredOnly={setMonitoredOnly}
      />

      {/* Analyzing bucket ABOVE the table — these are files subarr hasn't
          probed yet; surfacing them up top (with a Probe-now action) so
          they're seen, not buried under a long gap list. */}
      <CoverageBucket
        kind="unprobed"
        rows={analyzingRows}
        onProbeNow={handleProbeNow}
        probing={probing}
      />

      {/* Table — grows to fit its rows; the PAGE (main-canvas) scrolls
          rather than squeezing the table into a sliver. The column header
          is position:sticky so it stays pinned while the page scrolls. */}
      <div className="panel" style={{
        flexShrink: 0,
        minHeight: 240,
        padding: 0,
        display: 'flex', flexDirection: 'column',
        overflow: 'visible',
      }}>
        <div style={{ position: 'relative' }}>
          <CoverageHeader allSelected={selectedCount > 0 && selectedCount === rows.length} />
          {isInitialLoad && (
            <div style={{ padding: 40, textAlign: 'center', color: 'var(--fg-2)' }}>
              Loading coverage data…
            </div>
          )}
          {isError && (
            <div style={{ padding: 40, textAlign: 'center', color: 'var(--error-500)' }}>
              Couldn't load coverage: {String(error.message || error)}
              <div style={{ marginTop: 12 }}>
                <button className="btn" onClick={() => refetch()}>Retry</button>
              </div>
            </div>
          )}
          {isEmpty && (
            <div style={{ padding: 40, textAlign: 'center', color: 'var(--fg-2)' }}>
              {allRows && allRows.length === 0
                ? 'No gaps to address — every monitored file has its subs.'
                : analyzingRows.length
                  ? `No verified gaps yet — ${analyzingRows.length} file(s) still being analyzed (below).`
                  : 'No rows match the current filters.'}
            </div>
          )}
          {!isInitialLoad && !isError && groupBy === 'flat' && rows.map(r => (
            <CoverageRow
              key={r.id}
              r={r}
              onClick={toggleRow}
              onQueue={handleRowQueue}
              queuing={rowQueuing.has(r.id)}
            />
          ))}
          {!isInitialLoad && !isError && groupBy === 'tree' && (
            <CoverageTree
              rows={rows}
              selected={selected}
              toggleRow={toggleRow}
              onQueue={handleRowQueue}
              rowQueuing={rowQueuing}
              onDismissMixed={dismissMixed}
            />
          )}
        </div>
      </div>

      {/* "Couldn't analyze" failures stay below the table — exceptions,
          not the main flow. The "Analyzing" bucket moved ABOVE the table
          (see below) so it's visible without scrolling to the bottom. */}
      <CoverageBucket kind="probe_failed" rows={failedRows} />

      {/* #79: forced-only-EN rows the connected subgen will skip
          (IGNORE_FORCED_SUBTITLES off). Distinct, visible, non-actionable —
          not a fillable gap, not silently dropped. */}
      <CoverageBucket kind="forced_skip" rows={forcedSkipRows} />

      {/* Bottom selection bar — sits in page flow but sticky */}
      <div style={{ position: 'sticky', bottom: 16, marginTop: 0, marginBottom: 16 }}>
        <SelectionBar
          n={selectedCount}
          reasonFilter={reasonFilter}
          onClear={clearSelection}
          onQueue={handleBulkQueue}
          queueState={queueState}
        />
      </div>
    </main>
  );
}

