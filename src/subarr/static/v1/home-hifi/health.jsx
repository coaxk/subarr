// Health page (#157) — background-task supervision. subarr's long-running
// loops each record their per-cycle outcome to /api/health/tasks. A loop that
// quietly stops succeeding (the #79 class: catch-log-warning-and-keep-looping)
// shows up here red, with its captured traceback, instead of freezing in
// silence. The header pill links here when anything goes unhealthy.
//
// #291 Slice B: database maintenance section — on-demand full integrity check
// and VACUUM INTO backup. Red-pill callout when the db-integrity task is red.

import { apiFetch } from './api.jsx';
import { formatRecentRow } from './log-helpers.mjs';

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

// #252: a future timestamp as "in Xm" (or "due" when overdue). Returns null
// for event-driven loops (no next_run_at) so the column stays blank for them.
function timeUntil(ts) {
  if (!ts) return null;
  const s = Math.floor(ts - Date.now() / 1000);
  if (s <= 0) return 'due';
  if (s < 60) return `in ${s}s`;
  if (s < 3600) return `in ${Math.floor(s / 60)}m`;
  if (s < 86400) return `in ${Math.floor(s / 3600)}h`;
  return `in ${Math.floor(s / 86400)}d`;
}

const REPO = 'https://github.com/coaxk/subarr';

// Build a prefilled GitHub new-issue URL. We deliberately do NOT auto-embed
// the traceback (URL-length + it can contain local paths) — the user copies it
// and pastes it on GitHub, where they can review before posting. Privacy: the
// user curates exactly what leaves their box.
function reportUrl(title, body) {
  return `${REPO}/issues/new?labels=bug&title=${encodeURIComponent(title)}&body=${encodeURIComponent(body)}`;
}

function taskReportUrl(t, version) {
  const title = `Background task "${t.task_name}" unhealthy (${t.last_error_type || 'no success'})`;
  const body =
    `**Task:** ${t.task_name}${t.expected_interval_s ? ` (every ${fmtInterval(t.expected_interval_s)})` : ''}\n` +
    `**Error type:** ${t.last_error_type || '(no success recorded yet)'}\n` +
    `**Consecutive failures:** ${t.consecutive_failures}\n` +
    `**subarr version:** ${version || '?'}\n\n` +
    `**What I was doing / steps to reproduce:**\n\n\n` +
    `**Traceback** (copied from the Health page — please review for any local paths before posting):\n\n` +
    '```\n(paste the copied traceback here)\n```\n';
  return reportUrl(title, body);
}

const linkStyle = {
  fontSize: 'var(--text-sm)', color: 'var(--violet-400)', textDecoration: 'none',
};

function TaskRow({ t, version, onRun }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [running, setRunning] = useState(false);
  const unhealthy = t.is_unhealthy;
  const hasErr = !!t.last_error_detail;
  const doRun = (e) => {
    e.stopPropagation();
    setRunning(true);
    Promise.resolve(onRun && onRun(t.task_name)).finally(() => setRunning(false));
  };
  const copyTrace = (e) => {
    e.stopPropagation();
    navigator.clipboard.writeText(t.last_error_detail || '')
      .then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); })
      .catch(() => {});
  };
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
        {t.can_run_now && (
          <button onClick={doRun} disabled={running}
            title="Trigger this job now"
            className="btn sm" style={{ flex: 'none', fontSize: 'var(--text-2xs)' }}>
            {running ? '…' : 'Run now'}
          </button>
        )}
        <span style={{ flex: 'none', fontSize: 'var(--text-sm)', color: unhealthy ? 'var(--error-400, #f87171)' : 'var(--fg-3)' }}>
          {unhealthy ? 'unhealthy' : 'healthy'}
        </span>
        <span style={{ width: 130, textAlign: 'right', flex: 'none', fontSize: 'var(--text-sm)', color: 'var(--fg-3)' }}
          title="Last successful cycle">
          ok {timeAgo(t.last_success_at)}
        </span>
        <span style={{ width: 80, textAlign: 'right', flex: 'none', fontSize: 'var(--text-sm)', color: 'var(--fg-3)' }}
          title="Estimated next run (last success + cadence)">
          {t.next_run_at ? `next ${timeUntil(t.next_run_at)}` : ''}
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
          <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginTop: 10 }}>
            <button onClick={copyTrace} className="btn"
              style={{ fontSize: 'var(--text-xs)' }}>
              {copied ? '✓ copied' : 'copy traceback'}
            </button>
            <a href={taskReportUrl(t, version)} target="_blank" rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()} style={linkStyle}>
              Report this on GitHub ↗
            </a>
            <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>
              copy the traceback, then paste it into the issue (review for local paths first)
            </span>
          </div>
        </div>
      )}
    </React.Fragment>
  );
}

// #291 Slice B — db maintenance: full integrity check + VACUUM INTO backup.
// Advisory only; no writes are blocked regardless of check result.
function DbMaintenance({ dbIntegrityUnhealthy }) {
  const [integrityState, setIntegrityState] = useState(null); // null | 'running' | {ok, findings}
  const [backupState, setBackupState]       = useState(null); // null | 'running' | {path,size_bytes,created_at,pruned} | {error}

  const runIntegrityCheck = useCallback(() => {
    setIntegrityState('running');
    apiFetch('/api/admin/db/integrity-check', { method: 'POST' })
      .then((r) => r.ok ? r.json() : r.json().then((d) => Promise.reject(d.detail || `HTTP ${r.status}`)))
      .then((d) => setIntegrityState(d))
      .catch((e) => setIntegrityState({ ok: false, findings: [String(e)] }));
  }, []);

  const runBackup = useCallback(() => {
    setBackupState('running');
    apiFetch('/api/admin/db/backup', { method: 'POST' })
      .then((r) => r.ok ? r.json() : r.json().then((d) => Promise.reject(d.detail || `HTTP ${r.status}`)))
      .then((d) => setBackupState(d))
      .catch((e) => setBackupState({ error: String(e) }));
  }, []);

  const integrityDone   = integrityState && integrityState !== 'running';
  const integrityOk     = integrityDone && integrityState.ok;
  const integrityBad    = integrityDone && !integrityState.ok;

  const backupDone      = backupState && backupState !== 'running';
  const backupOk        = backupDone && !backupState.error;
  const backupErr       = backupDone && !!backupState.error;

  const fmtBytes = (n) => n < 1024 * 1024
    ? `${(n / 1024).toFixed(1)} KB`
    : `${(n / (1024 * 1024)).toFixed(2)} MB`;

  return (
    <section style={cardStyle}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <span className="label">Database maintenance</span>
      </div>

      {/* Red-pill callout when db-integrity task is unhealthy */}
      {dbIntegrityUnhealthy && (
        <div style={{
          display: 'flex', alignItems: 'flex-start', gap: 12,
          background: 'rgba(239,68,68,0.08)', border: '1px solid var(--error-500, #ef4444)',
          borderRadius: 'var(--radius-md)', padding: '10px 14px', marginBottom: 14,
        }}>
          <span style={{ color: 'var(--error-400, #f87171)', fontSize: 18, flex: 'none', lineHeight: 1 }}>⚠</span>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600, color: 'var(--error-300, #fca5a5)', fontSize: 'var(--text-sm)' }}>
              Database integrity issue detected
            </div>
            <div style={{ marginTop: 3, fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>
              Back up your data before making changes. Run a full integrity check to see what was found.
            </div>
            <button onClick={runBackup} disabled={backupState === 'running'}
              className="btn" style={{ marginTop: 8, fontSize: 'var(--text-xs)' }}>
              {backupState === 'running' ? 'Backing up…' : 'Back up now'}
            </button>
          </div>
        </div>
      )}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 12 }}>
        {/* Integrity check */}
        <div style={{ flex: '1 1 260px', background: 'var(--bg-0)', border: 'var(--border)', borderRadius: 'var(--radius-md)', padding: '12px 14px' }}>
          <div style={{ fontWeight: 600, fontSize: 'var(--text-sm)', marginBottom: 4 }}>Full integrity check</div>
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)', marginBottom: 10 }}>
            PRAGMA integrity_check — verifies index↔table consistency. Slower than the boot check; safe on a live database.
          </div>
          <button onClick={runIntegrityCheck} disabled={integrityState === 'running'}
            className="btn" style={{ fontSize: 'var(--text-xs)' }}>
            {integrityState === 'running' ? 'Checking…' : 'Run full integrity check'}
          </button>
          {integrityDone && (
            <div style={{ marginTop: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--text-sm)' }}>
                <span style={{ width: 8, height: 8, borderRadius: '50%', flex: 'none',
                  background: integrityOk ? 'var(--success-500, #22c55e)' : 'var(--error-500, #ef4444)' }} />
                <span style={{ color: integrityOk ? 'var(--success-400, #4ade80)' : 'var(--error-400, #f87171)', fontWeight: 600 }}>
                  {integrityOk ? 'No issues found' : 'Issues detected'}
                </span>
              </div>
              {integrityBad && (
                <pre style={{ marginTop: 6, padding: '6px 8px', background: '#0d0d10', borderRadius: 'var(--radius-sm)',
                  fontSize: 11, fontFamily: 'JetBrains Mono, monospace', whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all', color: 'var(--error-300, #fca5a5)', maxHeight: 180, overflow: 'auto' }}>
                  {(integrityState.findings || []).join('\n')}
                </pre>
              )}
            </div>
          )}
        </div>

        {/* Backup */}
        <div style={{ flex: '1 1 260px', background: 'var(--bg-0)', border: 'var(--border)', borderRadius: 'var(--radius-md)', padding: '12px 14px' }}>
          <div style={{ fontWeight: 600, fontSize: 'var(--text-sm)', marginBottom: 4 }}>Back up database</div>
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)', marginBottom: 10 }}>
            VACUUM INTO — writes a clean, consistent copy of the database. Safe while running. Keeps the 5 most recent backups in /data/backups/.
          </div>
          <button onClick={runBackup} disabled={backupState === 'running'}
            className="btn" style={{ fontSize: 'var(--text-xs)' }}>
            {backupState === 'running' ? 'Backing up…' : 'Back up database now'}
          </button>
          {backupDone && (
            <div style={{ marginTop: 10, fontSize: 'var(--text-xs)' }}>
              {backupOk ? (
                <div>
                  <div style={{ color: 'var(--success-400, #4ade80)', fontWeight: 600, marginBottom: 4 }}>Backup complete</div>
                  <div style={{ color: 'var(--fg-2)', wordBreak: 'break-all' }}>{backupState.path}</div>
                  <div style={{ color: 'var(--fg-3)', marginTop: 2 }}>{fmtBytes(backupState.size_bytes)}</div>
                  {backupState.pruned && backupState.pruned.length > 0 && (
                    <div style={{ color: 'var(--fg-3)', marginTop: 4 }}>
                      Pruned: {backupState.pruned.join(', ')}
                    </div>
                  )}
                </div>
              ) : (
                <div style={{ color: 'var(--error-400, #f87171)' }}>
                  Backup failed: {backupState.error}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-3)' }}>
        Backups are advisory. The integrity check does not block writes — it is a read-only diagnostic.
      </div>
    </section>
  );
}

// #157 gap-fill — recent WARN+ records from subarr's OWN log ring. Gives the
// CONTEXT around a red task that the single last-traceback can't. Fed by
// /api/logs/recent (the in-process LogRing snapshot).
function RecentErrors() {
  const [rows, setRows] = useState(null);
  const [open, setOpen] = useState({}); // id -> expanded

  const load = useCallback(() => {
    apiFetch('/api/logs/recent?level=WARNING&limit=200')
      .then((r) => (r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`)))
      .then((d) => setRows((d.records || []).map(formatRecentRow)))
      .catch(() => setRows([]));
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, [load]);

  const levelColor = (lvl) =>
    lvl === 'ERROR' || lvl === 'CRITICAL' ? 'var(--error-400, #f87171)'
      : lvl === 'WARNING' ? 'var(--warn-500, #f59e0b)'
      : 'var(--fg-3)';

  return (
    <section style={cardStyle}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
        <span className="label">Recent errors</span>
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-3)' }}>
          {rows == null ? 'loading…' : `${rows.length} WARN+`}
        </span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        {(rows || []).slice().reverse().map((row, i) => {
          const id = `${row.ts}-${i}`;
          const isOpen = !!open[id];
          return (
            <React.Fragment key={id}>
              <div onClick={() => row.hasTrace && setOpen((o) => ({ ...o, [id]: !o[id] }))}
                style={{
                  display: 'flex', alignItems: 'baseline', gap: 10, padding: '6px 10px',
                  borderRadius: 'var(--radius-md)', cursor: row.hasTrace ? 'pointer' : 'default',
                  fontFamily: 'JetBrains Mono, monospace', fontSize: 12,
                }}>
                <span style={{ flex: 'none', width: 64, color: levelColor(row.level), fontWeight: 600 }}>
                  {row.level}
                </span>
                <span style={{ flex: 'none', color: 'var(--fg-3)' }}>{timeAgo(row.ts)}</span>
                <span style={{ flex: 'none', color: 'var(--violet-400)' }}>{row.logger}</span>
                <span style={{ flex: 1, minWidth: 0, color: 'var(--fg-1)', wordBreak: 'break-all' }}>
                  {row.message}
                </span>
                <span style={{ flex: 'none', width: 14, color: 'var(--fg-3)' }}>
                  {row.hasTrace ? (isOpen ? '▾' : '▸') : ''}
                </span>
              </div>
              {isOpen && row.hasTrace && (
                <pre style={{
                  margin: '0 0 8px 74px', padding: '10px 12px', background: '#0d0d10',
                  border: 'var(--border)', borderRadius: 'var(--radius-md)',
                  whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                  fontFamily: 'JetBrains Mono, monospace', fontSize: 11, lineHeight: 1.45,
                  color: 'var(--fg-2)',
                }}>{row.exc_text}</pre>
              )}
            </React.Fragment>
          );
        })}
        {rows && rows.length === 0 && (
          <div style={{ color: 'var(--fg-3)', padding: 12 }}>No recent warnings or errors.</div>
        )}
      </div>
    </section>
  );
}

export function HealthPage() {
  const [tasks, setTasks] = useState(null);
  const [version, setVersion] = useState(null);
  const [err, setErr] = useState(null);

  const load = useCallback(() => {
    fetch('/api/health/tasks', { credentials: 'same-origin' })
      .then((r) => (r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`)))
      .then((d) => { setTasks(d.tasks || []); setVersion(d.version || null); setErr(null); })
      .catch((e) => setErr(String(e)));
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, [load]);

  // #234: trigger a runnable loop on demand, then reload status.
  const runJob = useCallback((name) =>
    fetch(`/api/health/tasks/${encodeURIComponent(name)}/run`, { method: 'POST', credentials: 'same-origin' })
      .then(() => load())
      .catch(() => {}), [load]);

  const unhealthy = (tasks || []).filter((t) => t.is_unhealthy).length;
  const dbIntegrityUnhealthy = (tasks || []).some((t) => t.task_name === 'db-integrity' && t.is_unhealthy);

  return (
    <main className="main-canvas" style={{ padding: '22px 24px 22px', gap: 14, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 'var(--text-h1)', fontWeight: 600 }}>Health</h1>
          <div style={{ marginTop: 4, fontSize: 'var(--text-sm)', color: 'var(--fg-2)', maxWidth: 760 }}>
            Background-task supervision. subarr's long-running loops (coverage + dashboard refresh, the scheduler, the completion watcher, the update + subgen checks) each report the outcome of every cycle here, so a loop that quietly stops working shows up red with its error instead of freezing in silence.
          </div>
        </div>
        <a href={reportUrl('Bug: ',
            `**What happened:**\n\n\n**Steps to reproduce:**\n\n\n` +
            `**subarr version:** ${version || '?'}\n**Environment:** (Docker / Unraid / ...)\n\n` +
            `_For a background-task failure: open Health, expand the task, copy its traceback, and paste it below._`)}
          target="_blank" rel="noopener noreferrer" className="btn"
          style={{ flex: 'none', fontSize: 'var(--text-sm)', textDecoration: 'none' }}>
          Report a problem ↗
        </a>
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
          {(tasks || []).map((t) => <TaskRow key={t.task_name} t={t} version={version} onRun={runJob} />)}
          {tasks && tasks.length === 0 && (
            <div style={{ color: 'var(--fg-3)', padding: 12 }}>No supervised tasks yet.</div>
          )}
        </div>
        <div style={{ marginTop: 10, fontSize: 'var(--text-sm)', color: 'var(--fg-3)' }}>
          A task is flagged unhealthy after 3 failed cycles in a row, or when it hasn't succeeded in 3x its normal interval. Click a failing task to see its last error.
        </div>
      </section>

      <RecentErrors />

      <DbMaintenance dbIntegrityUnhealthy={dbIntegrityUnhealthy} />
    </main>
  );
}

const cardStyle = { background: 'var(--bg-1)', border: 'var(--border)', borderRadius: 'var(--radius-lg)', padding: '16px 18px' };
const gateNoticeStyle = { background: 'rgba(245,158,11,0.10)', border: '1px solid var(--warn-500)', borderRadius: 'var(--radius-lg)', padding: '10px 12px', fontSize: 'var(--text-base)', color: 'var(--fg-1)' };
