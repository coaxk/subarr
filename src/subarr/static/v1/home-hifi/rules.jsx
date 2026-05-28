// Rules editor — Build / Test / Deploy triad.

import { StatusDot } from './atoms.jsx';

const { useState } = React;

const RULE = {
  name: 'nightly_walk',
  mode: 'manual_confirm',
  minScore: 6.5,
  maxPerRun: 50,
  schedule: 'every 4h',
  allow: ['eng', 'spa', 'fre'],
  deny: ['rus'],
  targets: ['/TV', '/Movies'],
  providers: 'opensubs → addic7ed → whisper',
};

const DRY_RUN = [
  { kind: 'queue', score: 9.4, title: 'Severance · S02E08',         why: 'no-track · eng,spa wanted',  prov: 'opensubs' },
  { kind: 'queue', score: 9.2, title: 'Shogun · S01E09',            why: 'embedded-only · eng wanted', prov: 'opensubs' },
  { kind: 'queue', score: 8.9, title: 'Dune: Part Two',             why: 'no-track · eng,fre wanted',  prov: 'opensubs' },
  { kind: 'queue', score: 8.6, title: 'Andor · S01E04',             why: 'bazarr-wanted · eng',        prov: 'opensubs' },
  { kind: 'queue', score: 8.4, title: 'Fallout · S01E03',           why: 'low-score · re-fetch',       prov: 'addic7ed' },
  { kind: 'queue', score: 8.2, title: 'Anora',                      why: 'no-track · eng wanted',      prov: 'opensubs' },
  { kind: 'queue', score: 7.9, title: '3 Body Problem · S01E05',    why: 'no-track · eng,zho wanted',  prov: 'opensubs' },
  { kind: 'queue', score: 7.6, title: 'Furiosa: A Mad Max Saga',    why: 'embedded-only · eng wanted', prov: 'opensubs' },
  { kind: 'skip',  score: 7.1, title: 'The Bikeriders',             why: 'unmonitored' },
  { kind: 'skip',  score: 6.3, title: 'Mr. & Mrs. Smith · S01E07',  why: 'audio rus → denied' },
  { kind: 'skip',  score: 5.9, title: 'X-Men ’97 · S01E04',         why: 'below min-score 6.5' },
  { kind: 'skip',  score: 5.6, title: 'Late Night with the Devil',  why: 'below min-score 6.5' },
];

const DEPLOY_DIFF = [
  { field: 'min_score',     from: '7.0',  to: '6.5' },
  { field: 'max_per_run',   from: '30',   to: '50' },
  { field: 'allow.languages', from: 'eng, spa', to: 'eng, spa, fre' },
];

// ─── Mode segmented control ──────────────────────────────────────
function ModeSegment({ mode, setMode }) {
  const MODES = [
    { id: 'build',  label: 'Build',  hint: 'define' },
    { id: 'test',   label: 'Test',   hint: 'dry-run' },
    { id: 'deploy', label: 'Deploy', hint: 'commit' },
  ];
  return (
    <div style={{
      display: 'flex',
      background: 'var(--bg-1)',
      border: 'var(--border)',
      borderRadius: 'var(--radius-md)',
      overflow: 'hidden',
      padding: 3,
      gap: 2,
    }}>
      {MODES.map(m => {
        const active = m.id === mode;
        return (
          <button key={m.id} onClick={() => setMode(m.id)} style={{
            display: 'flex', alignItems: 'baseline', gap: 8,
            padding: '0 16px',
            height: 30,
            borderRadius: 3,
            background: active ? 'var(--bg-3)' : 'transparent',
            color: active ? 'var(--fg-0)' : 'var(--fg-2)',
            fontSize: 'var(--text-md)',
            fontWeight: active ? 600 : 500,
            transition: 'background var(--dur-fast), color var(--dur-fast)',
            boxShadow: active ? 'inset 0 0 0 1px var(--bg-4)' : 'none',
          }}>
            <span>{m.label}</span>
            <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: active ? 'var(--fg-2)' : 'var(--fg-3)' }}>{m.hint}</span>
          </button>
        );
      })}
    </div>
  );
}

// ─── Form pieces ─────────────────────────────────────────────────
function Field({ label, value, mono = true, w, suffix, hint }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 5, width: w }}>
      <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)', letterSpacing: 0 }}>{label}</span>
      <div style={{
        display: 'flex', alignItems: 'center',
        height: 30, padding: '0 10px',
        background: 'var(--bg-2)',
        border: '1px solid var(--bg-4)',
        borderRadius: 'var(--radius-md)',
      }}>
        <span className={mono ? 'mono' : ''} style={{ flex: 1, fontSize: 'var(--text-md)', color: 'var(--fg-0)' }}>{value}</span>
        {suffix && <span className="mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>{suffix}</span>}
      </div>
      {hint && <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>{hint}</span>}
    </label>
  );
}

function ChipGroup({ items, kind, addLabel = '+ add' }) {
  return (
    <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', alignItems: 'center' }}>
      {items.map(it => (
        <span key={it} className={`chip ${kind || ''}`} style={{ height: 22, padding: '0 9px', fontSize: 'var(--text-xs)' }}>
          {it} <span style={{ marginLeft: 2, color: 'var(--fg-3)', cursor: 'pointer' }}>×</span>
        </span>
      ))}
      <button style={{
        fontSize: 'var(--text-xs)',
        color: 'var(--fg-2)',
        padding: '0 9px', height: 22,
        border: '1px dashed var(--bg-5)',
        borderRadius: 'var(--radius-pill)',
        background: 'transparent',
      }}>{addLabel}</button>
    </div>
  );
}

// ─── BUILD MODE ──────────────────────────────────────────────────
function ModeBuild() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <Section title="Identity">
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 200px 200px', gap: 14 }}>
          <Field label="rule name"  value={RULE.name} />
          <Field label="mode"       value={RULE.mode} suffix="enum" />
          <Field label="schedule"   value={RULE.schedule} />
        </div>
      </Section>

      <Section title="Thresholds">
        <div style={{ display: 'grid', gridTemplateColumns: '200px 200px 1fr', gap: 14 }}>
          <Field label="min score"        value="6.5" hint="Tautulli priority floor" />
          <Field label="max per run"      value="50"  hint="backpressure for subgen" />
          <Field label="provider chain"   value={RULE.providers} />
        </div>
      </Section>

      <Section title="Languages">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <div style={{ marginBottom: 6, fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>allow</div>
            <ChipGroup items={RULE.allow} kind="violet" />
          </div>
          <div>
            <div style={{ marginBottom: 6, fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>deny</div>
            <ChipGroup items={RULE.deny} kind="error" />
          </div>
        </div>
      </Section>

      <Section title="Targets">
        <ChipGroup items={RULE.targets} addLabel="+ add path" />
      </Section>

      <Section title="Advanced kwargs" muted>
        <div style={{
          padding: '10px 12px',
          background: 'var(--bg-0)',
          border: '1px solid var(--bg-4)',
          borderRadius: 'var(--radius-md)',
          fontFamily: 'var(--font-mono)',
          fontSize: 'var(--text-xs)',
          color: 'var(--fg-1)',
          lineHeight: 1.6,
        }}>
          <span style={{ color: 'var(--fg-3)' }}># forwarded verbatim to subgen</span><br />
          --whisper-model=<span style={{ color: 'var(--violet-400)' }}>large-v3</span> --vad=true --chunk=30<br />
          --hint-from-audio=true --normalise-punctuation=true
        </div>
      </Section>
    </div>
  );
}

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

// ─── TEST MODE ───────────────────────────────────────────────────
function ModeTest() {
  const queues = DRY_RUN.filter(d => d.kind === 'queue');
  const skips  = DRY_RUN.filter(d => d.kind === 'skip');
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Totals strip */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 16,
        padding: '14px 16px',
        background: 'var(--bg-2)',
        border: '1px solid var(--bg-4)',
        borderRadius: 'var(--radius-md)',
      }}>
        <div>
          <div className="label" style={{ marginBottom: 2 }}>would queue</div>
          <div className="display num" style={{ fontSize: 24, fontWeight: 500, color: 'var(--success-500)' }}>{queues.length}</div>
        </div>
        <div style={{ width: 1, height: 36, background: 'var(--bg-4)' }} />
        <div>
          <div className="label" style={{ marginBottom: 2 }}>would skip</div>
          <div className="display num" style={{ fontSize: 24, fontWeight: 500, color: 'var(--fg-2)' }}>{skips.length}</div>
        </div>
        <div style={{ width: 1, height: 36, background: 'var(--bg-4)' }} />
        <div>
          <div className="label" style={{ marginBottom: 2 }}>candidates evaluated</div>
          <div className="display num" style={{ fontSize: 24, fontWeight: 500, color: 'var(--fg-0)' }}>612</div>
        </div>
        <span style={{ flex: 1 }} />
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
          <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>ran 14:31:42 · against Bazarr state</span>
          <button className="btn sm">↻ Re-run dry</button>
        </div>
      </div>

      {/* Filter chips */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="chip violet" style={{ height: 22, padding: '0 9px' }}>queue ({queues.length})</span>
        <span className="chip" style={{ height: 22, padding: '0 9px' }}>skip ({skips.length})</span>
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>diff vs last run: <span className="num" style={{ color: 'var(--success-500)' }}>+2</span> queue · <span className="num" style={{ color: 'var(--fg-1)' }}>-1</span> skip</span>
      </div>

      {/* Result rows */}
      <div style={{
        border: 'var(--border)',
        borderRadius: 'var(--radius-md)',
        background: 'var(--bg-1)',
        overflow: 'hidden',
      }}>
        {DRY_RUN.map((d, i) => <DryRunRow key={i} d={d} last={i === DRY_RUN.length - 1} />)}
      </div>
    </div>
  );
}

function DryRunRow({ d, last }) {
  const isQueue = d.kind === 'queue';
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12,
      padding: '0 14px',
      height: 36,
      borderBottom: last ? 'none' : '1px solid var(--bg-3)',
    }}>
      <span style={{
        fontFamily: 'var(--font-mono)',
        fontSize: 'var(--text-xs)',
        fontWeight: 600,
        letterSpacing: '0.04em',
        width: 56,
        color: isQueue ? 'var(--success-500)' : 'var(--fg-3)',
      }}>{isQueue ? 'QUEUE' : 'SKIP'}</span>
      <span className="num mono" style={{
        width: 36, textAlign: 'right',
        fontSize: 'var(--text-base)',
        fontWeight: 600,
        color: isQueue ? 'var(--violet-400)' : 'var(--fg-3)',
      }}>{d.score.toFixed(1)}</span>
      <span style={{
        flex: 1,
        fontSize: 'var(--text-base)',
        color: isQueue ? 'var(--fg-0)' : 'var(--fg-2)',
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>{d.title}</span>
      <span className="mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>{d.why}</span>
      {d.prov && (
        <span className="chip" style={{ height: 18, padding: '0 6px', fontSize: 'var(--text-2xs)' }}>{d.prov}</span>
      )}
    </div>
  );
}

// ─── DEPLOY MODE ─────────────────────────────────────────────────
function ModeDeploy() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12,
        padding: '14px 16px',
        background: 'rgba(139,92,246,0.06)',
        border: '1px solid rgba(139,92,246,0.30)',
        borderRadius: 'var(--radius-md)',
      }}>
        <StatusDot kind="violet" />
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 'var(--text-md)', fontWeight: 600, color: 'var(--fg-0)' }}>
            Ready to commit
          </div>
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)', marginTop: 2 }}>
            3 fields changed. Next scheduled run will use the new rule.
          </div>
        </div>
        <span className="num mono" style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-1)' }}>
          next run · 4h 12m
        </span>
      </div>

      <Section title="Diff">
        <div style={{
          border: 'var(--border)',
          borderRadius: 'var(--radius-md)',
          overflow: 'hidden',
          background: 'var(--bg-1)',
        }}>
          {DEPLOY_DIFF.map((d, i) => (
            <div key={d.field} style={{
              display: 'flex', alignItems: 'center', gap: 14,
              padding: '0 14px',
              height: 38,
              borderBottom: i === DEPLOY_DIFF.length - 1 ? 'none' : '1px solid var(--bg-3)',
            }}>
              <span className="mono" style={{ width: 160, fontSize: 'var(--text-sm)', color: 'var(--fg-1)' }}>{d.field}</span>
              <span className="mono" style={{
                fontSize: 'var(--text-sm)',
                color: 'var(--error-500)',
                background: 'rgba(239,68,68,0.08)',
                padding: '2px 8px',
                borderRadius: 2,
                textDecoration: 'line-through',
                textDecorationColor: 'rgba(239,68,68,0.4)',
              }}>{d.from}</span>
              <span style={{ color: 'var(--fg-3)' }}>→</span>
              <span className="mono" style={{
                fontSize: 'var(--text-sm)',
                color: 'var(--success-500)',
                background: 'rgba(52,211,153,0.08)',
                padding: '2px 8px',
                borderRadius: 2,
              }}>{d.to}</span>
              <span style={{ flex: 1 }} />
            </div>
          ))}
        </div>
      </Section>

      <Section title="What will happen">
        <ul style={{
          margin: 0, padding: 0, listStyle: 'none',
          display: 'flex', flexDirection: 'column', gap: 8,
        }}>
          {[
            ['Save', 'Rule version v17 is written to subarr\'s database.'],
            ['Re-schedule', 'The scheduler picks up the change before the next run.'],
            ['Notify', 'Provenance ledger gets a single "rule edited" entry pointing to this diff.'],
          ].map(([k, v], i) => (
            <li key={k} style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
              <span className="num mono" style={{
                width: 18, height: 18,
                background: 'var(--bg-3)',
                borderRadius: 3,
                fontSize: 'var(--text-2xs)',
                color: 'var(--fg-1)',
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                marginTop: 1,
              }}>{i + 1}</span>
              <span>
                <span style={{ fontSize: 'var(--text-md)', fontWeight: 600, color: 'var(--fg-0)' }}>{k}.</span>
                <span style={{ fontSize: 'var(--text-md)', color: 'var(--fg-1)', marginLeft: 6 }}>{v}</span>
              </span>
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}

// ─── Page wrapper ────────────────────────────────────────────────
export function RulesPage() {
  const [mode, setMode] = useState('test');

  return (
    <main className="main-canvas" style={{ padding: '22px 24px 22px', gap: 18, overflow: 'auto' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--text-xs)', color: 'var(--fg-3)', marginBottom: 4 }}>
            <a href="#" style={{ color: 'var(--fg-2)', textDecoration: 'none' }}>Rules</a>
            <span>/</span>
            <span className="mono" style={{ color: 'var(--fg-1)' }}>nightly_walk</span>
          </div>
          <h1 style={{ margin: 0, fontSize: 22, lineHeight: 1.15, fontWeight: 600, letterSpacing: '-0.005em', display: 'flex', alignItems: 'baseline', gap: 10 }}>
            nightly_walk
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--warn-500)', fontWeight: 500, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
              dirty
            </span>
          </h1>
          <div style={{ marginTop: 4, display: 'flex', alignItems: 'center', gap: 10, fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>
            <span>last edited <span className="mono">14:02</span></span>
            <span style={{ width: 1, height: 12, background: 'var(--bg-4)' }} />
            <span>version <span className="mono">v16 → v17</span></span>
            <span style={{ width: 1, height: 12, background: 'var(--bg-4)' }} />
            <span>next run <span className="mono">4h 12m</span></span>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <button className="btn ghost">View provenance</button>
          <button className="btn">Discard</button>
          <button className="btn">Save draft</button>
        </div>
      </div>

      {/* Mode segment */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <ModeSegment mode={mode} setMode={setMode} />
        <span style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-3)' }}>
          {mode === 'build'  && 'Define the rule. Edits live here until you Test or Deploy.'}
          {mode === 'test'   && 'Dry-run against current Bazarr state. Nothing is queued.'}
          {mode === 'deploy' && 'Commit the rule. The scheduler will use it next.'}
        </span>
      </div>

      {/* Pane */}
      <div className="panel" style={{ padding: '22px 24px', flex: 1, minHeight: 0 }}>
        {mode === 'build'  && <ModeBuild />}
        {mode === 'test'   && <ModeTest />}
        {mode === 'deploy' && <ModeDeploy />}
      </div>

      {/* Deploy CTA — only on deploy mode */}
      {mode === 'deploy' && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12,
          padding: '12px 16px',
          background: 'var(--bg-1)',
          border: '1px solid var(--violet-500)',
          borderRadius: 'var(--radius-md)',
        }}>
          <StatusDot kind="violet" />
          <span style={{ fontSize: 'var(--text-md)', color: 'var(--fg-1)' }}>
            Commit changes to <span className="mono" style={{ color: 'var(--fg-0)' }}>nightly_walk</span> and schedule for <span className="mono">18:44</span>?
          </span>
          <span style={{ flex: 1 }} />
          <button className="btn">Cancel</button>
          <button className="btn primary">Commit & schedule</button>
        </div>
      )}
    </main>
  );
}

