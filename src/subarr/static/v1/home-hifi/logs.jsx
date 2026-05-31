// Live logs viewer — SSE-streamed from /api/logs/events.
// Filter by source, pause/resume, copy all, auto-scroll toggle.

const { useState, useEffect, useRef, useMemo } = React;

const LEVEL_COLORS = {
  ERROR: '#ef4444',
  WARN:  '#f59e0b',
  INFO:  '#9ca3af',
  DEBUG: '#6b7280',
};

// #209 fix: the SSE feed is the subgen container log stream — every line
// comes FROM subgen. Content-based source detection was misclassifying as
// 'subarr' for lines that didn't happen to contain the word 'subgen'. Tag
// everything 'subgen' since that's the actual source. When we later add
// multi-container streaming the backend should pass the source explicitly.
function classifyLine(line) {
  let level = 'INFO';
  if (line.match(/\bERROR\b/i)) level = 'ERROR';
  else if (line.match(/\bWARN(?:ING)?\b/i)) level = 'WARN';
  else if (line.match(/\bDEBUG\b/i)) level = 'DEBUG';
  return { source: 'subgen', level };
}

// Backend currently only streams the subgen container log. Other sources
// (subarr internal, bazarr, sonarr, …) aren't wired yet — show only the
// one selector that actually corresponds to a live stream rather than
// listing dead chips that filter to nothing.
const SOURCES = ['subgen'];

export function LogsPage() {
  const [lines, setLines] = useState([]);
  const [paused, setPaused] = useState(false);
  const [follow, setFollow] = useState(true);
  const [sources, setSources] = useState(new Set(SOURCES));
  const [search, setSearch] = useState('');
  const [connected, setConnected] = useState(false);
  const bufRef = useRef([]);
  const flushTimer = useRef(null);
  const viewRef = useRef(null);
  const pausedRef = useRef(paused);
  useEffect(() => { pausedRef.current = paused; }, [paused]);

  useEffect(() => {
    let es;
    try {
      es = new EventSource('/api/logs/events');
      es.onopen = () => setConnected(true);
      es.onerror = () => setConnected(false);
      // #209 fix: backend emits 'event: log' (not default 'message'), so we
      // listen on the named event channel. Also keep onmessage for any
      // future generic events.
      const onLine = (e) => {
        const t = Date.now();
        let text = e.data || '';
        // Backend JSON-encodes the line; strip the wrapping quotes if so.
        try { text = JSON.parse(text); } catch {}
        const { source, level } = classifyLine(text);
        bufRef.current.push({ t, text, source, level, id: t + '-' + Math.random() });
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
  }, []);

  useEffect(() => {
    if (follow && viewRef.current) {
      viewRef.current.scrollTop = viewRef.current.scrollHeight;
    }
  }, [lines, follow]);

  const filtered = useMemo(() => {
    const s = search.trim().toLowerCase();
    return lines.filter(l =>
      sources.has(l.source) && (!s || l.text.toLowerCase().includes(s))
    );
  }, [lines, sources, search]);

  const toggleSource = (src) => {
    setSources(prev => {
      const next = new Set(prev);
      if (next.has(src)) next.delete(src); else next.add(src);
      return next;
    });
  };

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
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>Logs</h1>
          <div style={{ marginTop: 4, fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>
            Live SSE stream from subarr + downstream services.
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
          <button key={src} onClick={() => toggleSource(src)}
            className={`chip ${sources.has(src) ? 'violet' : ''}`}
            style={{ cursor: 'pointer', textTransform: 'lowercase' }}>
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
        {filtered.length === 0 && (
          <div style={{ color: 'var(--fg-3)', textAlign: 'center', padding: 40 }}>
            {connected ? 'Waiting for log events…' : 'Connecting…'}
          </div>
        )}
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
