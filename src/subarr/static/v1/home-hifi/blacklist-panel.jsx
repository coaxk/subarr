// #317 Slice A: Bazarr-parity "blacklist a bad sub" panel.
//
// A shared, event-driven modal. Open it by dispatching:
//   window.dispatchEvent(new CustomEvent('open-blacklist', { detail: ref }))
// where ref is either { media_type, id, title } (Coverage/Review rows have the
// Bazarr ids) OR { path, title } (Aftercare passes the audited sub's path,
// which the backend resolves to the file's arr id via the coverage snapshot).
//
// It lists the file's Bazarr subtitle-download history and lets the user
// blacklist a provider sub so Bazarr stops re-fetching that broken release.
// Only provider downloads with a subs_id are blacklistable (manual uploads and
// already-blacklisted rows are shown but not actionable).

const { useState, useEffect, useCallback } = React;

function _historyQuery(ref) {
  if (ref && ref.path) return 'path=' + encodeURIComponent(ref.path);
  if (ref && ref.media_type && ref.id != null) return `media_type=${ref.media_type}&id=${ref.id}`;
  return null;
}

// #161 P3 (T8): build the blacklist endpoint + body for a history row. The
// opened item's canonical path (ref.path, supplied by aftercare/library openers)
// routes the Bazarr blacklist call to the instance owning that library; id-only
// openers (no path) omit it → instance 0 (back-compat). Pure (exported for test).
export function blacklistRequest(row, ref) {
  const isEp = row.episode_id != null || ref?.media_type === 'episode';
  const url = isEp ? '/api/blacklist/episode' : '/api/blacklist/movie';
  const body = isEp
    ? { series_id: row.series_id, episode_id: row.episode_id, provider: row.provider, subs_id: row.subs_id, language: row.language, subtitles_path: row.subtitles_path }
    : { radarr_id: row.radarr_id, provider: row.provider, subs_id: row.subs_id, language: row.language, subtitles_path: row.subtitles_path };
  if (ref?.path) body.canonical_path = ref.path;
  return { url, body };
}

export function BlacklistPanel() {
  const [ref, setRef] = useState(null);          // the open media ref, or null
  const [rows, setRows] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [busyKey, setBusyKey] = useState(null);  // subs_id in flight

  useEffect(() => {
    const onOpen = (e) => { setRef(e.detail || {}); };
    window.addEventListener('open-blacklist', onOpen);
    return () => window.removeEventListener('open-blacklist', onOpen);
  }, []);

  const close = useCallback(() => { setRef(null); setRows(null); setError(null); }, []);

  const load = useCallback(async (r) => {
    const q = _historyQuery(r);
    if (!q) { setError('Nothing to look up for this item.'); return; }
    setLoading(true); setError(null); setRows(null);
    try {
      const resp = await fetch('/api/blacklist/history?' + q, { credentials: 'same-origin' });
      if (resp.status === 503) throw new Error('Bazarr isn’t configured, so there’s no download history to blacklist against.');
      if (resp.status === 404) throw new Error('Couldn’t match this file to a Bazarr-linked series/movie.');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const d = await resp.json();
      setRows(d.subtitles || []);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { if (ref) load(ref); }, [ref, load]);

  const blacklist = useCallback(async (row) => {
    if (!row.blacklistable) return;
    if (!window.confirm(
      `Blacklist this ${row.provider} subtitle in Bazarr?\n\n`
      + `Bazarr will stop re-fetching this exact release and can search for a different one. Reversible from Bazarr.`
    )) return;
    setBusyKey(row.subs_id);
    const { url, body } = blacklistRequest(row, ref);
    try {
      const resp = await fetch(url, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin', body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const d = await resp.json().catch(() => ({}));
        throw new Error(d.detail || `HTTP ${resp.status}`);
      }
      // mark it done in place
      setRows((prev) => (prev || []).map((x) =>
        x.subs_id === row.subs_id ? { ...x, blacklisted: true, blacklistable: false } : x));
    } catch (e) {
      window.alert(`Blacklist failed: ${e.message || e}`);
    } finally {
      setBusyKey(null);
    }
  }, [ref]);

  if (!ref) return null;

  const title = ref.title || 'Subtitle history';
  return (
    <div onClick={close} style={{
      position: 'fixed', inset: 0, zIndex: 2000, background: 'rgba(0,0,0,0.55)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24,
    }}>
      <div onClick={(e) => e.stopPropagation()} className="panel" style={{
        width: 'min(720px, 96vw)', maxHeight: '82vh', overflow: 'auto',
        background: 'var(--bg-1)', border: 'var(--border)', borderRadius: 'var(--radius-lg)',
        padding: 18,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
          <span style={{ fontSize: 'var(--text-md)', fontWeight: 600, color: 'var(--fg-0)', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{title}</span>
          <button className="btn ghost sm" onClick={close} aria-label="Close">✕</button>
        </div>
        <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)', marginBottom: 12 }}>
          Bazarr download history — blacklist a bad provider sub so Bazarr stops re-fetching it.
        </div>

        {loading && <div style={{ color: 'var(--fg-3)', padding: 24, textAlign: 'center' }}>Loading history…</div>}
        {error && <div style={{ color: 'var(--error-500)', padding: 16, fontSize: 'var(--text-sm)' }}>{error}</div>}
        {rows && rows.length === 0 && !error && (
          <div style={{ color: 'var(--fg-3)', padding: 24, textAlign: 'center', fontSize: 'var(--text-sm)' }}>
            No Bazarr download history for this file.
          </div>
        )}
        {rows && rows.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {rows.map((row, i) => (
              <div key={row.subs_id || i} style={{
                display: 'flex', alignItems: 'center', gap: 12, padding: '8px 10px',
                background: 'var(--bg-2)', borderRadius: 'var(--radius-md)',
                opacity: row.blacklisted ? 0.6 : 1,
              }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-0)' }}>
                    {row.provider || 'unknown'}
                    {row.language && <span className="mono" style={{ color: 'var(--fg-3)', marginLeft: 8, fontSize: 'var(--text-2xs)' }}>{row.language}</span>}
                    {row.score != null && <span className="mono" style={{ color: 'var(--fg-3)', marginLeft: 8, fontSize: 'var(--text-2xs)' }}>score {row.score}</span>}
                  </div>
                  {row.timestamp && <div style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>{row.timestamp}</div>}
                </div>
                {row.blacklisted ? (
                  <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--warn-500, #f59e0b)' }}>blacklisted</span>
                ) : row.blacklistable ? (
                  <button className="btn sm" disabled={busyKey === row.subs_id}
                    onClick={() => blacklist(row)}
                    style={{ padding: '0 10px', fontSize: 'var(--text-2xs)' }}>
                    {busyKey === row.subs_id ? '…' : 'Blacklist'}
                  </button>
                ) : (
                  <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }} title="Manual upload or no provider id — Bazarr can't blacklist it">—</span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
