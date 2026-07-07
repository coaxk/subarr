// Live logs viewer — SSE-streamed from /api/logs/events.
// Filter by source, pause/resume, copy all, auto-scroll toggle.

const { useState, useEffect, useRef, useMemo } = React;

import { LOG_SOURCES } from './log-helpers.mjs';

const LEVEL_COLORS = {
  ERROR: '#ef4444',
  WARN:  '#f59e0b',
  INFO:  '#9ca3af',
  DEBUG: '#6b7280',
};

// Derive the level from the line text; the source is passed in (which stream
// this line came from), not guessed from content (#209).
function classifyLine(line, source) {
  let level = 'INFO';
  if (line.match(/\bERROR\b/i)) level = 'ERROR';
  else if (line.match(/\bWARN(?:ING)?\b/i)) level = 'WARN';
  else if (line.match(/\bDEBUG\b/i)) level = 'DEBUG';
  return { source, level };
}

// #157 gap-fill: the two live streams — subgen (container log) and subarr
// (subarr's own in-process ring). Single-select via the source switcher.
const SOURCES = LOG_SOURCES;  // ['subgen', 'subarr'] — #157 gap-fill

export function LogsPage() {
  const [lines, setLines] = useState([]);
  const [paused, setPaused] = useState(false);
  const [follow, setFollow] = useState(true);
  const [search, setSearch] = useState('');
  const [connected, setConnected] = useState(false);
  // #328: a server-sent docker-unavailable notice (subarr can't reach the
  // Docker socket to stream subgen's log). Holds the backend message; renders
  // a help panel instead of an endless "Connecting…".
  const [streamError, setStreamError] = useState(null);
  // #157 gap-fill: which log to stream — 'subgen' (container) or 'subarr'
  // (subarr's own in-process ring). The EventSource re-subscribes on change.
  const [source, setSource] = useState('subgen');
  const bufRef = useRef([]);
  const flushTimer = useRef(null);
  const viewRef = useRef(null);
  const pausedRef = useRef(paused);
  useEffect(() => { pausedRef.current = paused; }, [paused]);

  useEffect(() => {
    setLines([]);          // #157: clear the view when switching source
    bufRef.current = [];
    setStreamError(null);
    const endpoint = source === 'subarr' ? '/api/logs/subarr/events' : '/api/logs/events';
    let es;
    try {
      es = new EventSource(endpoint);
      es.onopen = () => { setConnected(true); setStreamError(null); };
      es.onerror = () => setConnected(false);
      // #328: subarr couldn't reach the Docker socket to stream subgen's log.
      // A dedicated event (not the transport 'error') carrying the reason.
      es.addEventListener('stream_error', (e) => {
        let msg = e.data || '';
        try { msg = JSON.parse(msg); } catch { /* already plain */ }
        setStreamError(msg || 'Docker is unavailable.');
        setConnected(false);
      });
      // #209 fix: backend emits 'event: log' (not default 'message'), so we
      // listen on the named event channel. Also keep onmessage for any
      // future generic events.
      const onLine = (e) => {
        const t = Date.now();
        let payload = e.data || '';
        try { payload = JSON.parse(payload); } catch {}
        // subgen streams a raw string line; subarr streams a structured record.
        const text = (payload && typeof payload === 'object')
          ? `${payload.level || 'INFO'} ${payload.logger_name || ''} ${payload.message || ''}`.trim()
          : String(payload);
        const { source: src, level } = classifyLine(text, source);
        bufRef.current.push({ t, text, source: src, level, id: t + '-' + Math.random() });
        if (!flushTimer.current) {
          flushTimer.current = setTimeout(() => {
            flushTimer.current = null;
            if (!pausedRef.current) {
              setLines(prev => {
                const next = prev.concat(bufRef.current);
                bufRef.current = [];
                return next.length > 5000 ? next.slice(-5000) : next;
              });
            }
          }, 200);
        }
      };
      es.addEventListener('log', onLine);
      es.onmessage = onLine;  // belt-and-braces in case backend changes
    } catch {}
    return () => { try { es && es.close(); } catch {} };
  }, [source]);

  useEffect(() => {
    if (follow && viewRef.current) {
      viewRef.current.scrollTop = viewRef.current.scrollHeight;
    }
  }, [lines, follow]);

  const filtered = useMemo(() => {
    const s = search.trim().toLowerCase();
    return lines.filter(l => !s || l.text.toLowerCase().includes(s));
  }, [lines, search]);

  const copyAll = () => {
    const text = filtered.map(l => l.text).join('\n');
    navigator.clipboard.writeText(text)
      .then(() => alert(`Copied ${filtered.length} lines.`))
      .catch(err => alert('Copy failed: ' + err.message));
  };

  return (
    <main className="main-canvas" style={{ padding: '22px 24px 0', gap: 12, overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 'var(--text-h1)', fontWeight: 600 }}>Logs</h1>
          <div style={{ marginTop: 4, fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>
            Live SSE stream — switch between subgen (container) and subarr (its own log).
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ fontSize: 'var(--text-xs)', color: connected ? '#22d3a1' : '#ef4444' }}>
            {connected ? '● live' : '○ disconnected'}
          </span>
          <button className="btn" onClick={() => setPaused(p => !p)}
            aria-label={paused ? 'Resume log stream' : 'Pause log stream'}>
            {paused ? '▶ resume' : '⏸ pause'}
          </button>
          <button className="btn" onClick={() => setLines([])}
            aria-label="Clear visible log lines">clear</button>
          <button className="btn" onClick={copyAll} disabled={!filtered.length}
            aria-label={`Copy ${filtered.length} log lines to clipboard`}>copy</button>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        {SOURCES.map(src => (
          <button key={src} onClick={() => setSource(src)}
            className={`chip ${source === src ? 'violet' : ''}`}
            style={{ cursor: 'pointer', textTransform: 'lowercase' }}
            aria-pressed={source === src}>
            {src}
          </button>
        ))}
        <span style={{ flex: 1 }} />
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
          <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
          auto-scroll
        </label>
        <input type="search" placeholder="search lines…" value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{
            padding: '6px 10px',
            background: 'var(--bg-1)', color: 'var(--fg-0)',
            border: 'var(--border)', borderRadius: 'var(--radius-md)',
            fontSize: 'var(--text-xs)', minWidth: 240,
          }} />
      </div>

      <div ref={viewRef} className="panel" style={{
        flex: 1, minHeight: 0, padding: '8px 12px', marginBottom: 16,
        background: '#0d0d10', overflowY: 'auto',
        fontFamily: 'JetBrains Mono, monospace',
        fontSize: 12, lineHeight: 1.45,
      }}>
        {filtered.length === 0 && (streamError ? (
          <div style={{ padding: 32, maxWidth: 660, margin: '0 auto' }}>
            <div style={{ color: 'var(--error-500, #ef4444)', fontWeight: 600, fontSize: 'var(--text-md)', marginBottom: 8 }}>
              Can't reach Docker
            </div>
            <div style={{ color: 'var(--fg-2)', fontSize: 'var(--text-sm)', lineHeight: 1.6 }}>
              The Logs viewer streams subgen's container log, which needs Docker
              socket access — and subarr's container can't reach it, so there's
              nothing to show here.
              <br /><br />
              Give the subarr container the socket: bind-mount{' '}
              <code style={{ color: 'var(--fg-1)' }}>/var/run/docker.sock</code>{' '}
              (read-only is fine) into subarr, or point it at a socket-proxy, then
              reload. Everything else in subarr works without it — only this Logs
              viewer needs it.
            </div>
            <div className="mono" style={{
              marginTop: 14, padding: '8px 10px', background: 'var(--bg-1)',
              borderRadius: 'var(--radius-md)', color: 'var(--fg-3)',
              fontSize: 'var(--text-2xs)', wordBreak: 'break-all',
            }}>
              {String(streamError)}
            </div>
          </div>
        ) : (
          <div style={{ color: 'var(--fg-3)', textAlign: 'center', padding: 40 }}>
            {connected ? 'Waiting for log events…' : 'Connecting…'}
          </div>
        ))}
        {filtered.map(l => (
          <div key={l.id} style={{
            whiteSpace: 'pre-wrap', wordBreak: 'break-all',
            color: LEVEL_COLORS[l.level] || 'var(--fg-1)',
          }}>
            <span style={{ color: 'var(--fg-3)', marginRight: 8 }}>[{l.source}]</span>
            {l.text}
          </div>
        ))}
        <div style={{ fontSize: 10, color: 'var(--fg-3)', textAlign: 'right', marginTop: 8 }}>
          {filtered.length} of {lines.length} lines
        </div>
      </div>
    </main>
  );
}
