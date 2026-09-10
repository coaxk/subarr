// #479: persistent, dismissable banner for a subgen outage. Rendered at the
// top of the dashboard from /api/home/dashboard's `subgen_outage`. Dismissal
// is keyed on the outage's start time, so a NEW outage shows again.
import { shouldShowOutage, outageMessage } from './subgen-outage.mjs';

const KEY = 'subarr.subgenOutageDismissedSince';

function readDismissed() {
  try {
    const v = window.localStorage.getItem(KEY);
    return v == null ? null : Number(v);
  } catch { return null; }
}

function writeDismissed(since) {
  try { window.localStorage.setItem(KEY, String(since)); } catch { /* storage unavailable */ }
}

export function SubgenOutageBanner({ outage }) {
  const [dismissedSince, setDismissedSince] = React.useState(readDismissed);
  if (!shouldShowOutage(outage, dismissedSince)) return null;
  const m = outageMessage(outage);
  return (
    <div role="alert"
      style={{
        display: 'flex', alignItems: 'flex-start', gap: 12,
        padding: '12px 14px', marginBottom: 12,
        background: 'rgba(239,68,68,0.06)',
        border: '1px solid rgba(239,68,68,0.4)',
        borderRadius: 'var(--radius-lg)',
        fontSize: 'var(--text-xs)', color: 'var(--fg-1)',
      }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ color: 'var(--error-500)', fontWeight: 600, fontSize: 'var(--text-sm)' }}>{m.title}</div>
        <div style={{ marginTop: 4 }}>{m.body}</div>
        {m.hint && <div style={{ marginTop: 4, color: 'var(--fg-2)' }}>{m.hint}</div>}
        <div style={{ marginTop: 6, color: 'var(--fg-3)' }}>
          Fix the URL under <a href="/settings#subgen" style={{ color: 'var(--violet-400)' }}>Settings → subgen</a>, or restart the container.
          This notice comes back for the next outage even if you dismiss it.
        </div>
      </div>
      <button className="btn ghost sm" aria-label="Dismiss" title="Dismiss for this outage"
        onClick={() => { writeDismissed(outage.since); setDismissedSince(outage.since); }}
        style={{ padding: '0 8px' }}>✕</button>
    </div>
  );
}
