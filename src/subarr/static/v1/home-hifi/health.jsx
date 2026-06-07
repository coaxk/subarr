// Health page (#157) — background-task supervision. subarr's long-running
// loops each record their per-cycle outcome to /api/health/tasks. A loop that
// quietly stops succeeding (the #79 class: catch-log-warning-and-keep-looping)
// shows up here red, with its captured traceback, instead of freezing in
// silence. The header pill links here when anything goes unhealthy.

const { useState, useEffect, useCallback } = React;

function timeAgo(ts) {
  if (!ts) return 'never';
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function fmtInterval(s) {
  if (!s) return null;
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${Math.round(s / 3600)}h`;
}

function TaskRow({ t }) {
  const [open, setOpen] = useState(false);
  const unhealthy = t.is_unhealthy;
  const hasErr = !!t.last_error_detail;
  return (
    <React.Fragment>
      <div onClick={() => hasErr && setOpen((o) => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: 12, padding: '10px 12px',
          borderRadius: 'var(--radius-md)', cursor: hasErr ? 'pointer' : 'default',
          background: unhealthy ? 'rgba(239,68,68,0.08)' : 'transparent',
        }}>
        <span style={{
          width: 8, height: 8, borderRadius: '50%', flex: 'none',
          background: unhealthy ? 'var(--error-500, #ef4444)' : 'var(--success-500, #22c55e)',
        }} />
        <span style={{ flex: 1, minWidth: 0, fontWeight: 600, color: unhealthy ? 'var(--fg-0)' : 'var(--fg-1)' }}>
          {t.task_name}
          {t.expected_interval_s ? (
            <span style={{ marginLeft: 8, fontSize: 'var(--text-2xs)', color: 'var(--fg-3)', fontWeight: 400 }}>
              every {fmtInterval(t.expected_interval_s)}
            </span>
          ) : null}
        </span>
        <span style={{ flex: 'none', fontSize: 'var(--text-sm)', color: unhealthy ? 'var(--error-400, #f87171)' : 'var(--fg-3)' }}>
          {unhealthy ? 'unhealthy' : 'healthy'}
        </span>
        <span style={{ width: 130, textAlign: 'right', flex: 'none', fontSize: 'var(--text-sm)', color: 'var(--fg-3)' }}
          title="Last successful cycle">
          ok {timeAgo(t.last_success_at)}
        </span>
        <span style={{ width: 90, textAlign: 'right', flex: 'none', fontSize: 'var(--text-sm)', color: t.consecutive_failures ? 'var(--error-400, #f87171)' : 'var(--fg-3)' }}
          title="Consecutive failed cycles">
          {t.consecutive_failures} fail{t.consecutive_failures === 1 ? '' : 's'}
        </span>
        <span style={{ width: 14, flex: 'none', color: 'var(--fg-3)' }}>{hasErr ? (open ? '▾' : '▸') : ''}</span>
      </div>
      {open && hasErr && (
        <div style={{
          margin: '0 0 8px 32px', padding: '10px 12px', background: '#0d0d10',
          border: 'var(--border)', borderRadius: 'var(--radius-md)',
        }}>
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)', marginBottom: 6 }}>
            last error: <b style={{ color: 'var(--error-400, #f87171)' }}>{t.last_error_type}</b> {'·'} {timeAgo(t.last_error_at)}
          </div>
          <pre style={{
            margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
            fontFamily: 'JetBrains Mono, monospace', fontSize: 11, lineHeight: 1.45, color: 'var(--fg-2)',
          }}>{t.last_error_detail}</pre>
        </div>
      )}
    </React.Fragment>
  );
}

export function HealthPage() {
  const [tasks, setTasks] = useState(null);
  const [err, setErr] = useState(null);

  const load = useCallback(() => {
    fetch('/api/health/tasks', { credentials: 'same-origin' })
      .then((r) => (r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`)))
      .then((d) => { setTasks(d.tasks || []); setErr(null); })
      .catch((e) => setErr(String(e)));
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, [load]);

  const unhealthy = (tasks || []).filter((t) => t.is_unhealthy).length;

  return (
    <main className="main-canvas" style={{ padding: '22px 24px 22px', gap: 14, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
      <div>
        <h1 style={{ margin: 0, fontSize: 'var(--text-h1)', fontWeight: 600 }}>Health</h1>
        <div style={{ marginTop: 4, fontSize: 'var(--text-sm)', color: 'var(--fg-2)', maxWidth: 760 }}>
          Background-task supervision. subarr's long-running loops (coverage + dashboard refresh, the scheduler, the completion watcher, the update + subgen checks) each report the outcome of every cycle here, so a loop that quietly stops working shows up red with its error instead of freezing in silence.
        </div>
      </div>

      {err && <div style={gateNoticeStyle}>Couldn't load task health: {err}</div>}

      <section style={cardStyle}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
          <span className="label">Background tasks</span>
          <span style={{ flex: 1 }} />
          <span style={{ fontSize: 'var(--text-sm)', color: unhealthy ? 'var(--error-400, #f87171)' : 'var(--success-400, #4ade80)' }}>
            {tasks == null ? 'loading…' : unhealthy ? `${unhealthy} unhealthy` : 'all healthy'}
          </span>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {(tasks || []).map((t) => <TaskRow key={t.task_name} t={t} />)}
          {tasks && tasks.length === 0 && (
            <div style={{ color: 'var(--fg-3)', padding: 12 }}>No supervised tasks yet.</div>
          )}
        </div>
        <div style={{ marginTop: 10, fontSize: 'var(--text-sm)', color: 'var(--fg-3)' }}>
          A task is flagged unhealthy after 3 failed cycles in a row, or when it hasn't succeeded in 3x its normal interval. Click a failing task to see its last error.
        </div>
      </section>
    </main>
  );
}

const cardStyle = { background: 'var(--bg-1)', border: 'var(--border)', borderRadius: 'var(--radius-lg)', padding: '16px 18px' };
const gateNoticeStyle = { background: 'rgba(245,158,11,0.10)', border: '1px solid var(--warn-500)', borderRadius: 'var(--radius-lg)', padding: '10px 12px', fontSize: 'var(--text-base)', color: 'var(--fg-1)' };
