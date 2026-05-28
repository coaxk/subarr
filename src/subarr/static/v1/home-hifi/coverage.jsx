// Coverage — flat dense gap-list table.

import { Glyph, StatusDot } from './atoms.jsx';

const { useState } = React;

// ─── Demo dataset ───────────────────────────────────────────────
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
  'low-score':      { fg: 'var(--fg-1)',       bg: 'var(--bg-2)',           br: 'var(--bg-4)',           label: 'low-score' },
  'unmonitored':    { fg: 'var(--fg-2)',       bg: 'transparent',           br: 'var(--bg-4)',           label: 'unmon' },
};

function ReasonChip({ r }) {
  const v = REASON_STYLE[r] || REASON_STYLE['no-track'];
  return (
    <span className="mono" style={{
      display: 'inline-block', padding: '1px 7px',
      borderRadius: 2,
      border: `1px solid ${v.br}`, color: v.fg, background: v.bg,
      fontSize: 'var(--text-2xs)', lineHeight: '15px',
      letterSpacing: '0.01em',
      whiteSpace: 'nowrap',
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
        <span key={l} className="chip" style={{ height: 16, padding: '0 6px', fontSize: 'var(--text-2xs)' }}>{l}</span>
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

function CoverageStrip() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 18 }}>
      <span className="label">coverage</span>
      <CoverageBar label="bazarr" pct={74} />
      <CoverageBar label="sonarr" pct={88} />
      <CoverageBar label="radarr" pct={62} />
      <span style={{ width: 1, height: 14, background: 'var(--bg-4)' }} />
      <span className="num" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
        <span style={{ color: 'var(--fg-0)', fontWeight: 600 }}>612</span> gaps · last walk <span className="mono">10:32</span>
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

function FilterBar({ groupBy, setGroupBy, filtered }) {
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
        <FilterChip>monitored</FilterChip>
        <FilterChip active onClose>reason: no-track</FilterChip>
        <FilterChip>type: tv</FilterChip>
        <FilterChip>missing: eng</FilterChip>
        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)', marginLeft: 2, cursor: 'pointer' }}>+ filter</span>
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
  langs:  72,
  mon:    34,
  disk:   34,
  emb:    34,
  audio:  78,
  reason: 96,
  action: 30,
};

function HeaderCell({ children, w, right, center }) {
  return (
    <div style={{
      width: w, flex: w ? '0 0 auto' : 1, minWidth: 0,
      textAlign: right ? 'right' : center ? 'center' : 'left',
      fontSize: 'var(--text-2xs)',
      letterSpacing: '0.10em',
      textTransform: 'uppercase',
      color: 'var(--fg-2)',
      fontWeight: 600,
    }}>{children}</div>
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
      <HeaderCell w={COL.score} right>score</HeaderCell>
      <HeaderCell w={COL.type} />
      <HeaderCell>title</HeaderCell>
      <HeaderCell w={COL.ep}>episode</HeaderCell>
      <HeaderCell w={COL.langs}>langs</HeaderCell>
      <HeaderCell w={COL.mon} center>mon</HeaderCell>
      <HeaderCell w={COL.disk} center>disk</HeaderCell>
      <HeaderCell w={COL.emb} center>emb</HeaderCell>
      <HeaderCell w={COL.audio}>audio</HeaderCell>
      <HeaderCell w={COL.reason}>reason</HeaderCell>
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

function CoverageRow({ r, onClick }) {
  return (
    <div className="cov-row" style={{
      display: 'flex', alignItems: 'center', gap: 10,
      padding: '0 12px',
      height: 34,
      borderBottom: '1px solid var(--bg-3)',
      background: r.sel ? 'rgba(139,92,246,0.05)' : 'transparent',
      cursor: 'pointer',
      transition: 'background var(--dur-fast) var(--ease-out)',
    }}
    onClick={onClick}>
      <div style={{ width: COL.check, flex: `0 0 ${COL.check}px` }}>
        <CheckBox checked={r.sel} />
      </div>
      <div className="num mono" style={{
        width: COL.score, flex: `0 0 ${COL.score}px`,
        textAlign: 'right',
        fontSize: 'var(--text-base)',
        fontWeight: 600,
        color: scoreColor(r.score),
      }}>{r.score.toFixed(1)}</div>
      <div style={{ width: COL.type, flex: `0 0 ${COL.type}px`, textAlign: 'center' }}>
        <TypeGlyph t={r.type} />
      </div>
      <div style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'baseline', gap: 6 }}>
        <span style={{
          fontSize: 'var(--text-base)',
          color: 'var(--fg-0)',
          fontWeight: 500,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>{r.title}</span>
        <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)', flex: '0 0 auto' }}>· {r.size}</span>
      </div>
      <div className="mono num" style={{ width: COL.ep, flex: `0 0 ${COL.ep}px`, fontSize: 'var(--text-xs)', color: 'var(--fg-1)' }}>
        {r.ep || '—'}
      </div>
      <div style={{ width: COL.langs, flex: `0 0 ${COL.langs}px` }}>
        <LangChips langs={r.langs} />
      </div>
      <div style={{ width: COL.mon, flex: `0 0 ${COL.mon}px`, textAlign: 'center' }}>
        <YesNo on={r.mon} />
      </div>
      <div style={{ width: COL.disk, flex: `0 0 ${COL.disk}px`, textAlign: 'center' }}>
        <YesNo on={r.disk} kind="ok" />
      </div>
      <div style={{ width: COL.emb, flex: `0 0 ${COL.emb}px`, textAlign: 'center' }}>
        <YesNo on={r.emb} kind="warn" />
      </div>
      <div className="mono" style={{ width: COL.audio, flex: `0 0 ${COL.audio}px`, fontSize: 'var(--text-xs)', color: 'var(--fg-1)' }}>
        {r.audio}
      </div>
      <div style={{ width: COL.reason, flex: `0 0 ${COL.reason}px` }}>
        <ReasonChip r={r.reason} />
      </div>
      <div style={{ width: COL.action, flex: `0 0 ${COL.action}px`, textAlign: 'center', color: 'var(--fg-3)', fontSize: 'var(--text-md)' }}>⋯</div>
    </div>
  );
}

// ─── Selection action bar ────────────────────────────────────────
function SelectionBar({ n, reasonFilter }) {
  if (!n) return null;
  return (
    <div style={{
      position: 'sticky', bottom: 0,
      display: 'flex', alignItems: 'center', gap: 12,
      padding: '10px 16px',
      background: 'var(--bg-2)',
      border: 'var(--border)',
      borderRadius: 'var(--radius-lg)',
      boxShadow: '0 -2px 12px rgba(0,0,0,0.25)',
    }}>
      <CheckBox checked />
      <span style={{ fontSize: 'var(--text-md)', fontWeight: 600 }}>{n} selected</span>
      <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>· filtered by <span className="mono">{reasonFilter}</span></span>
      <span style={{ flex: 1 }} />
      <button className="btn ghost">Clear</button>
      <button className="btn">Apply rule…</button>
      <button className="btn primary">Queue selected ({n})</button>
    </div>
  );
}

// ─── Page ────────────────────────────────────────────────────────
export function CoveragePage() {
  const [groupBy, setGroupBy] = useState('flat');
  const rows = COVERAGE_ROWS;
  const selectedCount = rows.filter(r => r.sel).length;

  return (
    <main className="main-canvas" style={{ padding: '22px 24px 0', gap: 14 }}>
      {/* Page header */}
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22, lineHeight: 1.15, fontWeight: 600, letterSpacing: '-0.005em' }}>Coverage</h1>
          <div style={{ marginTop: 4, fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>
            Files where subarr thinks a subtitle is missing or below threshold.
          </div>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <button className="btn">Export CSV</button>
          <button className="btn">Re-walk now</button>
        </div>
      </div>

      {/* Coverage strip */}
      <div className="panel" style={{ padding: '12px 16px' }}>
        <CoverageStrip />
      </div>

      {/* Filter bar */}
      <FilterBar groupBy={groupBy} setGroupBy={setGroupBy} filtered={rows.length} />

      {/* Table */}
      <div className="panel" style={{
        flex: 1, minHeight: 0,
        padding: 0,
        display: 'flex', flexDirection: 'column',
        overflow: 'hidden',
      }}>
        <div style={{ flex: 1, overflow: 'auto', position: 'relative' }}>
          <CoverageHeader allSelected={false} />
          {rows.map(r => <CoverageRow key={r.id} r={r} />)}
        </div>
      </div>

      {/* Bottom selection bar — sits in page flow but sticky */}
      <div style={{ position: 'sticky', bottom: 16, marginTop: 0, marginBottom: 16 }}>
        <SelectionBar n={selectedCount} reasonFilter="no-track" />
      </div>
    </main>
  );
}

