// Rules editor — Build / Test / Deploy triad wired to the live
// /api/schedule + /api/schedule/rules + /api/schedule/preview endpoints.
//
// Maps to subarr's single global AutoQueueRules + the coverage_walk
// schedule. Editing is local until you click "Save changes" — that
// PUTs the new rules to the backend. "Test" runs a real dry-run via
// /api/schedule/preview against current Bazarr state. Honest about
// what fields exist (no fake "version v16 → v17" + no fake provider
// chain — neither is in the backend).

import { StatusDot } from './atoms.jsx';

const { useState, useEffect, useCallback, useMemo } = React;

const RULE_MODES = ['off', 'dashboard', 'manual_confirm', 'auto_queue'];
const MODE_HINT = {
  off: 'Scheduler does nothing.',
  dashboard: 'Walk + collect coverage candidates only — never queue. Surfaces them in Coverage.',
  manual_confirm: 'Walk + collect, but require approval before queueing (Schedule → Pending).',
  auto_queue: 'Walk + queue eligible candidates automatically.',
};

const DEFAULT_RULES = {
  mode: 'dashboard',
  min_score: 200,
  allow_languages: [], deny_languages: [],
  allow_tags: [], deny_tags: [],
  require_monitored: true,
  skip_stale_disk: true,
  skip_embedded_en: true,
  max_per_run: 50,
};

// ─── Live data hooks ─────────────────────────────────────────────
function useSchedule() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const refetch = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch('/api/schedule', { credentials: 'same-origin' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json()); setError(null);
    } catch (e) { setError(e); } finally { setLoading(false); }
  }, []);
  useEffect(() => { refetch(); }, [refetch]);
  return { data, loading, error, refetch };
}

async function putRules(rules) {
  const r = await fetch('/api/schedule/rules', {
    method: 'PUT', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(rules),
  });
  if (!r.ok) {
    const text = await r.text().catch(() => '');
    throw new Error(`HTTP ${r.status}: ${text.slice(0, 200)}`);
  }
  return r.json();
}

async function patchCoverageSchedule(updates) {
  const r = await fetch('/api/schedule/coverage_walk', {
    method: 'PATCH', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  });
  if (!r.ok) {
    const text = await r.text().catch(() => '');
    throw new Error(`HTTP ${r.status}: ${text.slice(0, 200)}`);
  }
  return r.json();
}

async function fetchPreview() {
  const r = await fetch('/api/schedule/preview', {
    method: 'POST', credentials: 'same-origin',
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// ─── Diff calc ───────────────────────────────────────────────────
function diffObjects(a, b) {
  const fields = [];
  for (const k of new Set([...Object.keys(a || {}), ...Object.keys(b || {})])) {
    const av = a?.[k]; const bv = b?.[k];
    const same = Array.isArray(av) && Array.isArray(bv)
      ? av.length === bv.length && av.every((x, i) => x === bv[i])
      : av === bv;
    if (!same) fields.push({ field: k, from: av, to: bv });
  }
  return fields;
}

// ─── Mode segment ────────────────────────────────────────────────
function ModeSegment({ mode, setMode }) {
  const MODES = [
    { id: 'build', label: 'Build', hint: 'define' },
    { id: 'test', label: 'Test', hint: 'dry-run' },
    { id: 'deploy', label: 'Deploy', hint: 'commit' },
  ];
  return (
    <div style={{
      display: 'flex', background: 'var(--bg-1)',
      border: 'var(--border)', borderRadius: 'var(--radius-md)',
      overflow: 'hidden', padding: 3, gap: 2,
    }}>
      {MODES.map((m) => {
        const active = m.id === mode;
        return (
          <button key={m.id} onClick={() => setMode(m.id)} style={{
            display: 'flex', alignItems: 'baseline', gap: 8,
            padding: '0 16px', height: 30, borderRadius: 3,
            background: active ? 'var(--bg-3)' : 'transparent',
            color: active ? 'var(--fg-0)' : 'var(--fg-2)',
            fontSize: 'var(--text-md)', fontWeight: active ? 600 : 500,
            border: 'none', cursor: 'pointer',
          }}>
            <span>{m.label}</span>
            <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: active ? 'var(--fg-2)' : 'var(--fg-3)' }}>{m.hint}</span>
          </button>
        );
      })}
    </div>
  );
}

// ─── Form primitives ─────────────────────────────────────────────
function Section({ title, children, muted }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <span className="label" style={{ color: muted ? 'var(--fg-3)' : 'var(--fg-2)' }}>{title}</span>
        <span style={{ flex: 1, height: 1, background: 'var(--bg-3)' }} />
      </div>
      {children}
    </div>
  );
}

function NumField({ label, value, onChange, hint, w, min, max }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 5, width: w }}>
      <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>{label}</span>
      <input
        type="number" value={value ?? ''}
        min={min} max={max}
        onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
        style={{
          height: 30, padding: '0 10px',
          background: 'var(--bg-2)', border: '1px solid var(--bg-4)',
          borderRadius: 'var(--radius-md)',
          fontSize: 'var(--text-md)', color: 'var(--fg-0)',
          fontFamily: 'var(--font-mono)',
        }} />
      {hint && <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>{hint}</span>}
    </label>
  );
}

function TextField({ label, value, onChange, hint, w, mono = true }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 5, width: w }}>
      <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>{label}</span>
      <input
        type="text" value={value ?? ''}
        onChange={(e) => onChange(e.target.value)}
        style={{
          height: 30, padding: '0 10px',
          background: 'var(--bg-2)', border: '1px solid var(--bg-4)',
          borderRadius: 'var(--radius-md)',
          fontSize: 'var(--text-md)', color: 'var(--fg-0)',
          fontFamily: mono ? 'var(--font-mono)' : 'inherit',
        }} />
      {hint && <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>{hint}</span>}
    </label>
  );
}

function ChipListEditor({ items, onChange, kind, placeholder }) {
  const [draft, setDraft] = useState('');
  const add = () => {
    const v = draft.trim();
    if (!v) return;
    if (items.includes(v)) { setDraft(''); return; }
    onChange([...items, v]); setDraft('');
  };
  const remove = (it) => onChange(items.filter((x) => x !== it));
  return (
    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
      {items.map((it) => (
        <span key={it} className={`chip ${kind || ''}`} style={{ height: 22, padding: '0 9px', fontSize: 'var(--text-xs)' }}>
          {it}
          <span onClick={() => remove(it)} style={{ marginLeft: 4, color: 'var(--fg-3)', cursor: 'pointer' }}>×</span>
        </span>
      ))}
      <input
        type="text" value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } }}
        placeholder={placeholder || 'add…'}
        style={{
          height: 22, padding: '0 9px', minWidth: 90,
          background: 'transparent',
          border: '1px dashed var(--bg-5)',
          borderRadius: 'var(--radius-pill)',
          fontSize: 'var(--text-xs)', color: 'var(--fg-1)',
        }} />
    </div>
  );
}

// ProbeRootsEditor — paths the scheduled walk runs ffprobe over.
//
// THIS IS THE CRITICAL CONFIG for embedded-EN detection. When empty,
// the walk skips ffprobe entirely and skip_embedded_en has nothing
// to consult. When set to e.g. "TV,Movies", the walk re-probes those
// roots before building coverage — and any file with a confirmed
// embedded English track gets dropped from the gap list AND surfaces
// as "has subs" in Library views.
//
// Paths are relative to SUBARR_MEDIA_ROOT (i.e. /media/library inside
// the container). ProbeStore caches by (mtime, size) so re-walks only
// ffprobe new/changed files.
function ProbeRootsEditor({ value, onChange }) {
  // Internal: value is stored as a list[str] in the schedule model, but
  // we render it as a comma-separated string for editing comfort. On
  // commit (blur or Enter), we split + trim back into a list.
  const [draft, setDraft] = useState(value.join(', '));
  // Re-sync draft if value changes from outside (e.g. Discard).
  useEffect(() => { setDraft(value.join(', ')); }, [value.join(',')]);

  const commit = () => {
    const next = draft.split(',').map((p) => p.trim()).filter(Boolean);
    if (next.join(',') !== value.join(',')) onChange(next);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>probe roots</span>
        <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>
          {value.length === 0 ? 'empty → ffprobe SKIPPED on scheduled walks' : `${value.length} root${value.length === 1 ? '' : 's'} — ffprobe runs`}
        </span>
      </div>
      <input
        type="text"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); commit(); } }}
        placeholder="TV, Movies"
        style={{
          height: 30, padding: '0 10px',
          background: 'var(--bg-2)', border: '1px solid var(--bg-4)',
          borderRadius: 'var(--radius-md)',
          fontSize: 'var(--text-md)', color: 'var(--fg-0)',
          fontFamily: 'var(--font-mono)',
        }} />
      <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>
        Comma-separated paths relative to your library root. Walk runs ffprobe
        on each, populating the embedded-sub cache that "skip embedded EN"
        relies on. Re-walks are cheap — cached files with unchanged mtime
        and size skip the probe. Leave empty for Bazarr-only coverage.
      </span>
    </div>
  );
}

function ToggleRow({ label, hint, on, onToggle }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 0', borderBottom: '1px solid var(--bg-3)' }}>
      <div>
        <div style={{ fontSize: 'var(--text-md)', color: 'var(--fg-0)' }}>{label}</div>
        {hint && <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)', marginTop: 2 }}>{hint}</div>}
      </div>
      <button onClick={onToggle}
        role="switch"
        aria-checked={!!on}
        aria-label={`Toggle ${label}`}
        style={{
        width: 36, height: 20, borderRadius: 99,
        background: on ? 'var(--violet-500)' : 'var(--bg-4)',
        position: 'relative', border: 'none', cursor: 'pointer', padding: 0,
      }}>
        <span style={{
          position: 'absolute', top: 2, left: on ? 18 : 2,
          width: 16, height: 16, background: '#fff', borderRadius: '50%',
          transition: 'left var(--dur-fast)',
        }} />
      </button>
    </div>
  );
}

// ─── BUILD MODE ──────────────────────────────────────────────────
function ModeBuild({ draft, setDraft, scheduleDraft, setScheduleDraft }) {
  const set = (k, v) => setDraft({ ...draft, [k]: v });
  const setSched = (k, v) => setScheduleDraft({ ...scheduleDraft, [k]: v });
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <Section title="Mode">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8 }}>
          {RULE_MODES.map((m) => {
            const active = draft.mode === m;
            return (
              <button key={m} onClick={() => set('mode', m)} style={{
                display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 4,
                padding: '10px 12px',
                background: active ? 'rgba(139,92,246,0.10)' : 'var(--bg-2)',
                border: `1px solid ${active ? 'var(--violet-500)' : 'var(--bg-4)'}`,
                borderRadius: 'var(--radius-md)',
                cursor: 'pointer', textAlign: 'left',
              }}>
                <span className="mono" style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: active ? 'var(--fg-0)' : 'var(--fg-1)' }}>{m}</span>
                <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>{MODE_HINT[m]}</span>
              </button>
            );
          })}
        </div>
      </Section>

      <Section title="Thresholds">
        <div style={{ display: 'grid', gridTemplateColumns: '200px 200px 200px', gap: 14 }}>
          <NumField label="min score" value={draft.min_score} onChange={(v) => set('min_score', v)}
            hint="Tautulli priority floor (0–1000)" min={0} max={1000} />
          <NumField label="max per run" value={draft.max_per_run} onChange={(v) => set('max_per_run', v)}
            hint="caps queue size per walk" min={1} max={500} />
        </div>
      </Section>

      <Section title="Languages">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <div style={{ marginBottom: 6, fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>allow (only these languages count as gaps)</div>
            <ChipListEditor items={draft.allow_languages || []} onChange={(v) => set('allow_languages', v)} kind="violet" placeholder="add language…" />
          </div>
          <div>
            <div style={{ marginBottom: 6, fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>deny (suppress these — common: English when you only want foreign subs)</div>
            <ChipListEditor items={draft.deny_languages || []} onChange={(v) => set('deny_languages', v)} kind="error" placeholder="add language…" />
          </div>
        </div>
      </Section>

      <Section title="Tags">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <div style={{ marginBottom: 6, fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>allow tags</div>
            <ChipListEditor items={draft.allow_tags || []} onChange={(v) => set('allow_tags', v)} kind="violet" placeholder="add tag…" />
          </div>
          <div>
            <div style={{ marginBottom: 6, fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>deny tags</div>
            <ChipListEditor items={draft.deny_tags || []} onChange={(v) => set('deny_tags', v)} kind="error" placeholder="add tag…" />
          </div>
        </div>
      </Section>

      <Section title="Filters">
        <ToggleRow label="Require monitored"
          hint="Only consider items Sonarr/Radarr is monitoring"
          on={!!draft.require_monitored} onToggle={() => set('require_monitored', !draft.require_monitored)} />
        <ToggleRow label="Skip stale disk"
          hint="Don't queue when subarr's probe found a sibling .srt Bazarr hasn't seen yet"
          on={!!draft.skip_stale_disk} onToggle={() => set('skip_stale_disk', !draft.skip_stale_disk)} />
        <ToggleRow label="Skip embedded EN"
          hint="Don't queue when the file has a full English embedded track"
          on={!!draft.skip_embedded_en} onToggle={() => set('skip_embedded_en', !draft.skip_embedded_en)} />
      </Section>

      {scheduleDraft && (
        <Section title="Schedule (coverage_walk)">
          <ToggleRow label="Enabled"
            hint="When off, the scheduler never runs — manual Re-walk still works"
            on={!!scheduleDraft.enabled} onToggle={() => setSched('enabled', !scheduleDraft.enabled)} />
          <div style={{ display: 'grid', gridTemplateColumns: '200px 200px 200px', gap: 14 }}>
            <TextField label="kind" value={scheduleDraft.kind} onChange={(v) => setSched('kind', v)} hint="daily | interval | weekly" />
            <NumField label="interval minutes" value={scheduleDraft.interval_minutes} onChange={(v) => setSched('interval_minutes', v)} hint="used when kind=interval" min={1} />
            <TextField label="daily HH:MM" value={scheduleDraft.daily_hhmm} onChange={(v) => setSched('daily_hhmm', v)} hint="used when kind=daily" />
          </div>
          <ProbeRootsEditor
            value={scheduleDraft.probe_roots || []}
            onChange={(v) => setSched('probe_roots', v)} />
        </Section>
      )}
    </div>
  );
}

// ─── TEST MODE ───────────────────────────────────────────────────
function ModeTest({ preview, loading, error, onRerun }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 16,
        padding: '14px 16px', background: 'var(--bg-2)',
        border: '1px solid var(--bg-4)', borderRadius: 'var(--radius-md)',
      }}>
        <div>
          <div className="label" style={{ marginBottom: 2 }}>would queue</div>
          <div className="display num" style={{ fontSize: 24, fontWeight: 500, color: 'var(--success-500, var(--violet-400))' }}>
            {loading ? '…' : (preview?.would_queue ?? '—')}
          </div>
        </div>
        <div style={{ width: 1, height: 36, background: 'var(--bg-4)' }} />
        <div>
          <div className="label" style={{ marginBottom: 2 }}>would skip</div>
          <div className="display num" style={{ fontSize: 24, fontWeight: 500, color: 'var(--fg-2)' }}>
            {loading ? '…' : (preview?.would_skip ?? '—')}
          </div>
        </div>
        <div style={{ width: 1, height: 36, background: 'var(--bg-4)' }} />
        <div>
          <div className="label" style={{ marginBottom: 2 }}>candidates evaluated</div>
          <div className="display num" style={{ fontSize: 24, fontWeight: 500, color: 'var(--fg-0)' }}>
            {loading ? '…' : (preview?.considered ?? '—')}
          </div>
        </div>
        <span style={{ flex: 1 }} />
        <button className="btn sm" onClick={onRerun} disabled={loading}>{loading ? 'Running…' : '↻ Re-run dry'}</button>
      </div>

      {error && (
        <div style={{ padding: 16, color: 'var(--error-500)', background: 'rgba(239,68,68,0.06)', borderRadius: 'var(--radius-md)' }}>
          Preview failed: {String(error.message || error)}
        </div>
      )}

      {preview && (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span className="chip violet" style={{ height: 22, padding: '0 9px' }}>queue ({preview.queue_preview?.length || 0})</span>
            <span className="chip" style={{ height: 22, padding: '0 9px' }}>skip sample ({preview.skip_sample?.length || 0})</span>
            <span style={{ flex: 1 }} />
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>preview is a dry-run — nothing is queued</span>
          </div>
          <div style={{ border: 'var(--border)', borderRadius: 'var(--radius-md)', background: 'var(--bg-1)', overflow: 'hidden' }}>
            {(preview.queue_preview || []).map((d, i, arr) => (
              <DryRunRow key={`q-${i}`} d={d} kind="queue" last={false} />
            ))}
            {(preview.skip_sample || []).map((d, i, arr) => (
              <DryRunRow key={`s-${i}`} d={d} kind="skip" last={i === arr.length - 1} />
            ))}
            {(preview.queue_preview?.length || 0) + (preview.skip_sample?.length || 0) === 0 && (
              <div style={{ padding: 40, textAlign: 'center', color: 'var(--fg-2)' }}>
                No candidates. {preview.considered === 0 ? 'Coverage is empty — nothing to evaluate.' : 'All candidates were already filtered out.'}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function DryRunRow({ d, kind, last }) {
  const isQueue = kind === 'queue';
  const title = d.title || d.canonical_path || '—';
  const why = d.reason || d.reasons?.join(', ') || '';
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12,
      padding: '0 14px', height: 36,
      borderBottom: last ? 'none' : '1px solid var(--bg-3)',
    }}>
      <span style={{
        fontFamily: 'var(--font-mono)', fontSize: 'var(--text-xs)', fontWeight: 600,
        letterSpacing: '0.04em', width: 56,
        color: isQueue ? 'var(--success-500, var(--violet-400))' : 'var(--fg-3)',
      }}>{isQueue ? 'QUEUE' : 'SKIP'}</span>
      <span className="num mono" style={{
        width: 44, textAlign: 'right',
        fontSize: 'var(--text-base)', fontWeight: 600,
        color: isQueue ? 'var(--violet-400)' : 'var(--fg-3)',
      }}>{d.score != null ? Math.min(9.9, d.score / 100).toFixed(1) : '—'}</span>
      <span style={{
        flex: 1, fontSize: 'var(--text-base)',
        color: isQueue ? 'var(--fg-0)' : 'var(--fg-2)',
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>{title}</span>
      <span className="mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)', maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{why}</span>
    </div>
  );
}

// ─── DEPLOY MODE ─────────────────────────────────────────────────
function ModeDeploy({ rulesDiff, scheduleDiff, saving, onSave, onDiscard }) {
  const fmt = (v) => v === null || v === undefined ? '—' : Array.isArray(v) ? (v.length ? v.join(', ') : '∅') : String(v);
  const total = rulesDiff.length + scheduleDiff.length;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12,
        padding: '14px 16px',
        background: total > 0 ? 'rgba(139,92,246,0.06)' : 'var(--bg-2)',
        border: `1px solid ${total > 0 ? 'rgba(139,92,246,0.30)' : 'var(--bg-4)'}`,
        borderRadius: 'var(--radius-md)',
      }}>
        <StatusDot kind={total > 0 ? 'violet' : 'muted'} />
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 'var(--text-md)', fontWeight: 600, color: 'var(--fg-0)' }}>
            {total > 0 ? `${total} field${total === 1 ? '' : 's'} changed` : 'No changes'}
          </div>
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)', marginTop: 2 }}>
            {total > 0 ? 'Save commits to /api/schedule/rules (and /api/schedule/coverage_walk if needed).' : 'Edit something in Build mode to see a diff here.'}
          </div>
        </div>
      </div>

      {(rulesDiff.length > 0 || scheduleDiff.length > 0) && (
        <Section title="Diff">
          <div style={{ border: 'var(--border)', borderRadius: 'var(--radius-md)', overflow: 'hidden', background: 'var(--bg-1)' }}>
            {[...rulesDiff.map((d) => ({ ...d, scope: 'rule' })), ...scheduleDiff.map((d) => ({ ...d, scope: 'schedule' }))].map((d, i, arr) => (
              <div key={`${d.scope}-${d.field}`} style={{
                display: 'flex', alignItems: 'center', gap: 14,
                padding: '0 14px', height: 38,
                borderBottom: i === arr.length - 1 ? 'none' : '1px solid var(--bg-3)',
              }}>
                <span className="mono" style={{ width: 80, fontSize: 'var(--text-2xs)', color: 'var(--fg-3)', textTransform: 'uppercase' }}>{d.scope}</span>
                <span className="mono" style={{ width: 180, fontSize: 'var(--text-sm)', color: 'var(--fg-1)' }}>{d.field}</span>
                <span className="mono" style={{
                  fontSize: 'var(--text-sm)', color: 'var(--error-500)',
                  background: 'rgba(239,68,68,0.08)', padding: '2px 8px', borderRadius: 2,
                  textDecoration: 'line-through', textDecorationColor: 'rgba(239,68,68,0.4)',
                }}>{fmt(d.from)}</span>
                <span style={{ color: 'var(--fg-3)' }}>→</span>
                <span className="mono" style={{
                  fontSize: 'var(--text-sm)', color: 'var(--success-500, var(--violet-400))',
                  background: 'rgba(52,211,153,0.08)', padding: '2px 8px', borderRadius: 2,
                }}>{fmt(d.to)}</span>
                <span style={{ flex: 1 }} />
              </div>
            ))}
          </div>
        </Section>
      )}

      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <span style={{ flex: 1 }} />
        <button className="btn ghost" onClick={onDiscard} disabled={total === 0 || saving}>Discard changes</button>
        <button className="btn primary" onClick={onSave} disabled={total === 0 || saving}>
          {saving ? 'Saving…' : 'Save changes'}
        </button>
      </div>
    </div>
  );
}

// ─── Friendly header — 4 panels above the editor ─────────────────
//
// The rules editor is a cold wall of fields without context: most users
// land here, scan the toggles, and bounce because nothing connects to
// "what's happening on MY library RIGHT NOW." Mirror the dashboard's
// 4-panel pattern: 1 welcome card (what this page IS, with quick
// presets) + 1 status row (mode / schedule / last result / active
// filters). Same visual language as Home so the user feels at home.

const MODE_TINT = {
  off:            { dot: 'muted',  fg: 'var(--fg-2)',     copy: 'doing nothing right now' },
  dashboard:      { dot: 'info',   fg: 'var(--cyan-400)', copy: 'walking, but never queueing' },
  manual_confirm: { dot: 'warn',   fg: 'var(--warn-500)', copy: 'walking, asking before queue' },
  auto_queue:     { dot: 'ok',     fg: 'var(--violet-400)', copy: 'walking + queueing eligible files' },
};

function HeaderTile({ label, value, sub, tint, tip }) {
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
        fontSize: 22, lineHeight: 1.05, fontWeight: 500,
        color: 'var(--fg-0)', letterSpacing: '-0.01em',
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>{value}</div>
      <div style={{
        fontSize: 'var(--text-xs)', color: 'var(--fg-2)',
        minHeight: 16,
      }}>{sub}</div>
    </div>
  );
}

function fmtNextRun(ts) {
  if (!ts) return null;
  const diff = ts - Date.now() / 1000;
  if (diff <= 0) return 'now';
  const m = Math.floor(diff / 60);
  if (m < 60) return `in ${m} min`;
  const h = Math.floor(m / 60);
  if (h < 48) return `in ${h} h`;
  return `in ${Math.floor(h / 24)} d`;
}

function RulesStatusRow({ serverRules, serverSchedule, preview }) {
  // Status tiles read SERVER-side state, not the in-flight draft, so
  // the user sees what's actually live RIGHT NOW. Once they Save, this
  // updates automatically from the refetch.
  const mode = serverRules?.mode || 'off';
  const tint = MODE_TINT[mode] || MODE_TINT.off;

  const schedEnabled = !!serverSchedule?.enabled;
  const schedKind = serverSchedule?.kind || 'unscheduled';
  const nextRunRel = fmtNextRun(serverSchedule?.next_run_at);

  const lastQueue = preview?.would_queue;
  const lastSkip = preview?.would_skip;
  const lastConsidered = preview?.considered;

  // Active filter count = anything constraining the candidate set.
  const r = serverRules || {};
  const filterParts = [];
  if (r.require_monitored) filterParts.push('monitored');
  if (r.skip_stale_disk)   filterParts.push('stale-disk');
  if (r.skip_embedded_en)  filterParts.push('embedded-en');
  const allowL = (r.allow_languages || []).length;
  const denyL  = (r.deny_languages  || []).length;
  const allowT = (r.allow_tags || []).length;
  const denyT  = (r.deny_tags  || []).length;
  const totalChips = allowL + denyL + allowT + denyT;

  return (
    <div style={{ display: 'flex', gap: 12 }}>
      <HeaderTile
        label="mode"
        value={mode.replace('_', ' ')}
        sub={tint.copy}
        tint={tint.dot}
        tip="What the scheduled walk is allowed to do. Off = nothing. Dashboard = walk only. Manual confirm = walk + ask. Auto-queue = walk + queue."
      />
      <HeaderTile
        label="schedule"
        value={schedEnabled ? schedKind : 'paused'}
        sub={schedEnabled
          ? (nextRunRel ? `next run ${nextRunRel}` : 'next run pending')
          : 'manual re-walk still works'}
        tint={schedEnabled ? 'ok' : 'muted'}
        tip="When the next coverage walk fires. Pause to halt automation without losing the rules — manual walks from the Coverage page still run."
      />
      <HeaderTile
        label="last dry-run"
        value={preview ? `${lastQueue} would queue` : '—'}
        sub={preview
          ? `${lastSkip} skipped · ${lastConsidered} candidates considered`
          : 'switch to Test to project against current Bazarr state'}
        tint={preview ? (lastQueue > 0 ? 'violet' : 'muted') : undefined}
        tip="Result of the most recent dry-run preview. Switch to Test mode (below) to refresh."
      />
      <HeaderTile
        label="active filters"
        value={`${filterParts.length + totalChips}`}
        sub={[
          filterParts.length && `${filterParts.length} toggle${filterParts.length === 1 ? '' : 's'}`,
          allowL + denyL > 0 && `${allowL + denyL} lang chip${allowL + denyL === 1 ? '' : 's'}`,
          allowT + denyT > 0 && `${allowT + denyT} tag chip${allowT + denyT === 1 ? '' : 's'}`,
        ].filter(Boolean).join(' · ') || 'no filters — everything qualifies'}
        tint={(filterParts.length + totalChips) > 0 ? 'info' : 'muted'}
        tip="Everything narrowing the candidate set: require-monitored, skip-stale-disk, skip-embedded-en, plus per-language and per-tag allow/deny lists."
      />
    </div>
  );
}

// Presets — one-click rule bundles users can apply. The buttons mutate
// the local rulesDraft so the user can still review + Test before Save.
const PRESETS = [
  {
    id: 'conservative',
    label: 'Conservative',
    sub: 'Walk + ask before queue. Sonarr/Radarr monitored only.',
    apply: (d) => ({
      ...d, mode: 'manual_confirm',
      require_monitored: true, skip_stale_disk: true, skip_embedded_en: true,
      min_score: 250,
    }),
  },
  {
    id: 'aggressive',
    label: 'Aggressive',
    sub: 'Auto-queue everything eligible up to 200/run.',
    apply: (d) => ({
      ...d, mode: 'auto_queue',
      require_monitored: true, skip_stale_disk: true, skip_embedded_en: true,
      min_score: 100, max_per_run: 200,
    }),
  },
  {
    id: 'foreign-only',
    label: 'Foreign-only',
    sub: 'Deny English subs — only queue when missing non-English.',
    apply: (d) => ({
      ...d, mode: 'auto_queue',
      deny_languages: Array.from(new Set([...(d.deny_languages || []), 'eng', 'en'])),
      require_monitored: true,
    }),
  },
  // #121: curated rule packs for common workflows. These build on the three
  // archetypes above with more opinionated defaults targeting specific
  // user needs.
  {
    id: 'anime',
    label: 'Anime',
    sub: 'Auto-queue Japanese audio missing English subs. Whisper translates.',
    apply: (d) => ({
      ...d, mode: 'auto_queue',
      allow_languages: ['jpn', 'ja'],
      deny_languages: (d.deny_languages || []).filter(l => !['eng', 'en'].includes(l)),
      skip_embedded_en: false,  // EN sub IS the goal, not a skip signal
      require_monitored: true,
      skip_stale_disk: true,
      min_score: 150,
    }),
  },
  {
    id: 'hearing-impaired',
    label: 'Hearing-impaired',
    sub: 'Prefer SDH/CC subs from Bazarr; Whisper only when none exist.',
    apply: (d) => ({
      ...d, mode: 'manual_confirm',  // Bazarr first; Whisper is fallback
      require_monitored: true,
      skip_stale_disk: true,
      skip_embedded_en: true,  // Already has SDH-tagged embedded sub? Skip.
      min_score: 200,
    }),
  },
  {
    id: 'foreign-language-learner',
    label: 'Language learner',
    sub: 'Original audio + English subs. Allows English only, transcribe task.',
    apply: (d) => ({
      ...d, mode: 'auto_queue',
      allow_languages: ['eng', 'en'],
      deny_languages: [],
      skip_embedded_en: true,  // Don't double-create when already there
      require_monitored: true,
      min_score: 150,
    }),
  },
  {
    id: 'movies-only',
    label: 'Movies only',
    sub: 'Scope to your movies tree; boost recently-watched. TV is excluded.',
    apply: (d) => ({
      ...d, mode: 'auto_queue',
      require_monitored: true,
      skip_stale_disk: true,
      skip_embedded_en: true,
      // No path-allowlist field yet; users wanting strict scoping should
      // pair this preset with a path-filter rule in v1.2. For now, the
      // higher min_score + Tautulli scoring delta does most of the work.
      min_score: 300,
      max_per_run: 50,
    }),
  },
];

function RulesWelcomeCard({ rulesDraft, setRulesDraft, mode, setMode }) {
  const [dismissed, setDismissed] = useState(() => {
    try { return localStorage.getItem('subarr.rules.welcome.dismissed') === '1'; }
    catch { return false; }
  });
  if (dismissed) return null;
  const dismiss = () => {
    try { localStorage.setItem('subarr.rules.welcome.dismissed', '1'); } catch {}
    setDismissed(true);
  };
  return (
    <div style={{
      background: 'linear-gradient(135deg, rgba(139,92,246,0.10), rgba(34,211,161,0.05))',
      border: '1px solid rgba(139,92,246,0.30)',
      borderRadius: 'var(--radius-lg)',
      padding: '18px 20px',
      display: 'flex', flexDirection: 'column', gap: 14,
    }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <span style={{ fontSize: 22, lineHeight: 1 }}>🤖</span>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 'var(--text-lg)', fontWeight: 600, color: 'var(--fg-0)' }}>
            Your background subtitle bot.
          </div>
          <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)', marginTop: 4, lineHeight: 1.5 }}>
            Auto-queue rules tell subarr what to do on every scheduled walk. Pick a starting preset
            below — or hand-tune the fields under <b>Build</b>, dry-run in <b>Test</b>, and ship in <b>Deploy</b>.
            Nothing changes on the server until you click Save.
          </div>
        </div>
        <button className="btn ghost" onClick={dismiss}
          title="Hide this card on this device."
          style={{ fontSize: 'var(--text-2xs)' }}>got it</button>
      </div>
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
        gap: 10,
      }}>
        {PRESETS.map((p) => (
          <div key={p.id} style={{
            background: 'var(--bg-1)', border: 'var(--border)',
            borderRadius: 'var(--radius-md)', padding: 12,
            display: 'flex', flexDirection: 'column', gap: 8,
          }}>
            <div style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--fg-0)' }}>
              {p.label}
            </div>
            <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)', flex: 1 }}>
              {p.sub}
            </div>
            <div>
              <button className="btn" style={{
                fontSize: 'var(--text-2xs)', padding: '4px 10px',
                background: 'var(--violet-500)', color: '#fff',
              }} onClick={() => {
                if (!rulesDraft) return;
                setRulesDraft(p.apply(rulesDraft));
                setMode('build');  // bounce to Build so user sees what changed
              }} disabled={!rulesDraft}>
                Apply preset
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Page wrapper ────────────────────────────────────────────────
export function RulesPage() {
  const { data, loading, error, refetch } = useSchedule();
  const [mode, setMode] = useState('build');

  const serverRules = data?.rules || null;
  const serverSchedule = (data?.schedules || []).find((s) => s.name === 'coverage_walk') || null;

  const [rulesDraft, setRulesDraft] = useState(null);
  const [scheduleDraft, setScheduleDraft] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);

  // Hydrate drafts from server data once it arrives.
  useEffect(() => {
    if (serverRules && !rulesDraft) setRulesDraft({ ...DEFAULT_RULES, ...serverRules });
    if (serverSchedule && !scheduleDraft) setScheduleDraft({ ...serverSchedule });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverRules, serverSchedule]);

  // Preview state for Test mode.
  const [preview, setPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState(null);
  const runPreview = useCallback(async () => {
    setPreviewLoading(true); setPreviewError(null);
    try { setPreview(await fetchPreview()); }
    catch (e) { setPreviewError(e); }
    finally { setPreviewLoading(false); }
  }, []);
  // Lazy-run preview when entering Test mode for the first time.
  useEffect(() => {
    if (mode === 'test' && !preview && !previewLoading) runPreview();
  }, [mode, preview, previewLoading, runPreview]);

  const rulesDiff = useMemo(() => rulesDraft && serverRules ? diffObjects(serverRules, rulesDraft) : [], [serverRules, rulesDraft]);
  const scheduleDiff = useMemo(() => scheduleDraft && serverSchedule ? diffObjects(serverSchedule, scheduleDraft) : [], [serverSchedule, scheduleDraft]);
  const dirtyCount = rulesDiff.length + scheduleDiff.length;

  const save = useCallback(async () => {
    setSaving(true); setSaveError(null);
    try {
      if (rulesDiff.length > 0) await putRules(rulesDraft);
      if (scheduleDiff.length > 0) {
        // PATCH only the changed fields. probe_roots needs to ship as a
        // comma-string per the backend schema (it splits/joins at the
        // store layer; PATCH endpoint expects str).
        const patch = {};
        for (const d of scheduleDiff) {
          if (d.field === 'probe_roots' && Array.isArray(d.to)) {
            patch[d.field] = d.to.join(',');
          } else {
            patch[d.field] = d.to;
          }
        }
        await patchCoverageSchedule(patch);
      }
      await refetch();
      setRulesDraft(null); setScheduleDraft(null); // re-hydrate from new server state
    } catch (e) {
      setSaveError(e);
    } finally {
      setSaving(false);
    }
  }, [rulesDraft, rulesDiff, scheduleDraft, scheduleDiff, refetch]);

  const discard = useCallback(() => {
    if (serverRules) setRulesDraft({ ...DEFAULT_RULES, ...serverRules });
    if (serverSchedule) setScheduleDraft({ ...serverSchedule });
  }, [serverRules, serverSchedule]);

  if (loading && !data) {
    return <main className="main-canvas" style={{ padding: 40, color: 'var(--fg-2)' }}>Loading rules…</main>;
  }
  if (error && !data) {
    return (
      <main className="main-canvas" style={{ padding: 40 }}>
        <div style={{ color: 'var(--error-500)' }}>Couldn't load schedule: {String(error.message || error)}</div>
        <button className="btn" onClick={refetch} style={{ marginTop: 12 }}>Retry</button>
      </main>
    );
  }

  return (
    <main className="main-canvas" style={{ padding: '22px 24px 22px', gap: 18, overflow: 'auto' }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--text-xs)', color: 'var(--fg-3)', marginBottom: 4 }}>
            <span style={{ color: 'var(--fg-2)' }}>Rules</span>
            <span>/</span>
            <span className="mono" style={{ color: 'var(--fg-1)' }}>auto-queue</span>
          </div>
          <h1 style={{ margin: 0, fontSize: 22, lineHeight: 1.15, fontWeight: 600, display: 'flex', alignItems: 'baseline', gap: 10 }}>
            Auto-queue rules
            {dirtyCount > 0 && (
              <span style={{ fontSize: 'var(--text-xs)', color: 'var(--warn-500)', fontWeight: 500, letterSpacing: '0.06em', textTransform: 'uppercase' }}>dirty</span>
            )}
          </h1>
          <div style={{ marginTop: 4, fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>
            {serverSchedule?.next_run_at ? (
              <>next run <span className="mono">{new Date(serverSchedule.next_run_at * 1000).toLocaleString()}</span></>
            ) : 'Schedule disabled — manual Re-walk still works from Coverage.'}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <button className="btn" onClick={discard} disabled={dirtyCount === 0 || saving}>Discard</button>
          <button className="btn primary" onClick={save} disabled={dirtyCount === 0 || saving}>
            {saving ? 'Saving…' : `Save${dirtyCount > 0 ? ` (${dirtyCount})` : ''}`}
          </button>
        </div>
      </div>

      {/* #195/#211-follow-up: 4-panel friendly header. Status row gives
          users at-a-glance "what's running right now" before they touch
          anything. Welcome card explains the page + offers presets so
          first-time users aren't dropped into a cold form. */}
      <RulesStatusRow
        serverRules={serverRules}
        serverSchedule={serverSchedule}
        preview={preview}
      />
      <RulesWelcomeCard
        rulesDraft={rulesDraft}
        setRulesDraft={setRulesDraft}
        mode={mode}
        setMode={setMode}
      />

      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <ModeSegment mode={mode} setMode={setMode} />
        <span style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-3)' }}>
          {mode === 'build' && 'Edit the rule. Changes are local until you Save.'}
          {mode === 'test' && 'Dry-run against current Bazarr state. Nothing is queued.'}
          {mode === 'deploy' && 'Review the diff. Save commits to the backend.'}
        </span>
      </div>

      {saveError && (
        <div style={{ padding: 12, color: 'var(--error-500)', background: 'rgba(239,68,68,0.06)', borderRadius: 'var(--radius-md)' }}>
          Save failed: {String(saveError.message || saveError)}
        </div>
      )}

      <div className="panel" style={{ padding: '22px 24px', flex: 1, minHeight: 0 }}>
        {mode === 'build' && rulesDraft && <ModeBuild draft={rulesDraft} setDraft={setRulesDraft} scheduleDraft={scheduleDraft} setScheduleDraft={setScheduleDraft} />}
        {mode === 'test' && <ModeTest preview={preview} loading={previewLoading} error={previewError} onRerun={runPreview} />}
        {mode === 'deploy' && <ModeDeploy rulesDiff={rulesDiff} scheduleDiff={scheduleDiff} saving={saving} onSave={save} onDiscard={discard} />}
      </div>
    </main>
  );
}
