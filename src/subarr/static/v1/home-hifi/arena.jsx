// #131 — Tuning Lab: drive a subgen config sweep against the live model and
// rank the outputs with the validated tournament judge.
//
// Flow: pick a media file + choose configs to compare (curated recipes and/or
// custom kwargs) → POST /api/arena/run → stream /events (SSE) for live
// progress → render the ranked scorecard. Rides subgen's v4.10 /asr path-input
// channel: no upload, nothing written to the library.
//
// Styling note: uses the home-hifi token system (--bg-*, --fg-*, --border,
// --radius-lg, --success-500, --violet-500) so the page matches the rest of
// the app — this is the marquee feature, not a bolt-on.

import { SectionCard, StatusDot, Glyph, ICONS } from './atoms.jsx';

const { useState, useEffect, useRef, useCallback } = React;

// Curated, purpose-built recipes the user toggles into a sweep. Named with a
// plain reason so nobody has to hand-write JSON. (Seed of the eventual
// crowd-curated, per-language set — see #124.)
const CURATED = [
  { id: 'default', label: 'default', kwargs: {}, why: 'Your current subgen settings, unchanged. The baseline every other recipe has to beat.' },
  { id: 'noisy', label: 'noisy-robust', kwargs: { vad_filter: true, beam_size: 5, temperature: 0 }, why: 'Skips non-speech and searches harder. Best for music or effects-heavy or low-quality audio.' },
  { id: 'anti-repeat', label: 'anti-repeat', kwargs: { condition_on_previous_text: false, compression_ratio_threshold: 2.4 }, why: 'Stops the model parroting the previous line. Reach for it when the output loops or drifts.' },
  { id: 'accurate', label: 'high-accuracy', kwargs: { beam_size: 8, best_of: 5, temperature: 0 }, why: 'Widest search. Slowest of the bunch, but squeezes out the most accurate read.' },
  { id: 'halluc-guard', label: 'hallucination-guard', kwargs: { vad_filter: true, condition_on_previous_text: false, no_speech_threshold: 0.6 }, why: 'Aggressive about silence with no carry-over. For sparse dialogue or long quiet stretches.' },
  { id: 'fast', label: 'fast-draft', kwargs: { beam_size: 1 }, why: 'Quick single pass. Good for a fast sanity check, not for final quality.' },
];
const DEFAULT_SELECTED = ['default', 'noisy', 'anti-repeat'];

const KNOBS = [
  ['beam_size', 'a number, 1 to 10', 'How hard it searches for the best wording. Higher is more accurate but slower. Around 5 is a sensible top end.'],
  ['vad_filter', 'on or off', 'Skips silence and non-speech before transcribing. The single biggest win against invented lines over music or quiet.'],
  ['temperature', '0.0 to 1.0', 'How much it guesses. 0 is steady and usually best; it only climbs higher as a fallback.'],
  ['condition_on_previous_text', 'on or off', 'Whether each line is shaped by the one before it. Turn off when the output repeats or wanders.'],
  ['compression_ratio_threshold', 'a number near 2.4', 'Catches gibberish and looping. Lower is stricter about throwing out junk.'],
  ['initial_prompt', 'some text', 'Primes names, jargon and tone. Handy for niche or technical content.'],
];

// Spoken-language picklist (same vocabulary as the Review page). Value is the
// ISO code we send; the user only ever sees the name.
const LANGS = [
  ['ara', 'Arabic'], ['bul', 'Bulgarian'], ['chi', 'Chinese'], ['hrv', 'Croatian'], ['cze', 'Czech'],
  ['dan', 'Danish'], ['dut', 'Dutch'], ['eng', 'English'], ['fin', 'Finnish'], ['fre', 'French'],
  ['ger', 'German'], ['gre', 'Greek'], ['heb', 'Hebrew'], ['hin', 'Hindi'], ['hun', 'Hungarian'],
  ['ind', 'Indonesian'], ['ita', 'Italian'], ['jpn', 'Japanese'], ['kor', 'Korean'], ['nor', 'Norwegian'],
  ['pol', 'Polish'], ['por', 'Portuguese'], ['rum', 'Romanian'], ['rus', 'Russian'], ['srp', 'Serbian'],
  ['spa', 'Spanish'], ['swe', 'Swedish'], ['tha', 'Thai'], ['tur', 'Turkish'], ['ukr', 'Ukrainian'], ['vie', 'Vietnamese'],
];

function parseKwargs(text) {
  const t = (text || '').trim();
  if (!t) return { ok: true, value: {} };
  try {
    const v = JSON.parse(t);
    if (v === null || typeof v !== 'object' || Array.isArray(v)) return { ok: false, error: 'must be a JSON object like {"beam_size": 5}' };
    return { ok: true, value: v };
  } catch (e) { return { ok: false, error: 'invalid JSON' }; }
}

function Hint({ children }) {
  return <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-3)', marginTop: 4, lineHeight: 1.5 }}>{children}</div>;
}

function StatusPill({ status }) {
  const map = {
    pending: { kind: 'idle', text: 'Pending' }, running: { kind: 'busy', text: 'Running' },
    done: { kind: 'ok', text: 'Done' }, error: { kind: 'err', text: 'Error' },
  };
  const s = map[status] || map.pending;
  return <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}><StatusDot kind={s.kind} pulse={status === 'running'} />{s.text}</span>;
}

// Collapsible card matching SectionCard styling, default-open.
function Collapsible({ label, defaultOpen = true, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section style={cardStyle}>
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open}
              style={{ display: 'flex', alignItems: 'center', width: '100%', background: 'transparent', border: 'none', cursor: 'pointer', padding: 0 }}>
        <span className="label">{label}</span>
        <span style={{ flex: 1 }} />
        <span style={{ color: 'var(--fg-3)', fontSize: 'var(--text-sm)' }}>{open ? '▾' : '▸'}</span>
      </button>
      {open && <div style={{ marginTop: 14 }}>{children}</div>}
    </section>
  );
}

// ── file picker modal ────────────────────────────────────────────────────────
const VIDEO_RE = /\.(mkv|mp4|avi|m4v|mov|webm|ts)$/i;

function FilePicker({ onPick, onClose }) {
  const [path, setPath] = useState('');
  const [entries, setEntries] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let alive = true;
    setLoading(true); setErr(null);
    fetch('/api/browse?path=' + encodeURIComponent(path), { credentials: 'same-origin' })
      .then((r) => r.json()).then((d) => { if (alive) setEntries(d.entries || []); })
      .catch((e) => { if (alive) setErr(String(e)); }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [path]);

  const parts = path ? path.split('/') : [];
  const dirs = (entries || []).filter((e) => e.is_dir);
  const files = (entries || []).filter((e) => !e.is_dir && VIDEO_RE.test(e.name));

  return (
    <div style={modalBackdrop} onClick={onClose}>
      <div style={modalCard} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <strong style={{ fontSize: 'var(--text-lg)' }}>Pick a media file</strong>
          <button onClick={onClose} style={iconBtnStyle}><Glyph char={ICONS.close || '×'} size={14} /></button>
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, fontSize: 'var(--text-sm)', marginBottom: 10 }}>
          <button onClick={() => setPath('')} style={crumbStyle}>library</button>
          {parts.map((p, i) => (
            <span key={i} style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
              <span style={{ color: 'var(--fg-3)' }}>/</span>
              <button onClick={() => setPath(parts.slice(0, i + 1).join('/'))} style={crumbStyle}>{p}</button>
            </span>
          ))}
        </div>
        <div style={{ minHeight: 220, maxHeight: 420, overflow: 'auto', border: 'var(--border)', borderRadius: 'var(--radius-lg)' }}>
          {loading ? <div style={pickRowMuted}>Loading…</div>
            : err ? <div style={{ ...pickRowMuted, color: 'var(--error-500)' }}>Couldn’t browse: {err}</div>
            : (dirs.length === 0 && files.length === 0) ? <div style={pickRowMuted}>No folders or video files here.</div>
            : (<>
                {dirs.map((e) => (
                  <button key={e.path} onClick={() => setPath(e.path)} style={pickRow}>
                    <span style={{ marginRight: 8 }}>📁</span>{e.name}
                    {e.video_count ? <span style={{ color: 'var(--fg-3)', marginLeft: 8, fontSize: 'var(--text-xs)' }}>{e.video_count} video{e.video_count === 1 ? '' : 's'}</span> : null}
                  </button>
                ))}
                {files.map((e) => (
                  <button key={e.path} onClick={() => onPick(e.path)} style={{ ...pickRow, color: 'var(--fg-0)' }}>
                    <span style={{ marginRight: 8 }}>🎬</span>{e.name}
                  </button>
                ))}
              </>)}
        </div>
        <Hint>A short, representative clip (a few minutes with real dialogue) compares recipes just as well as a whole episode, and finishes far faster.</Hint>
      </div>
    </div>
  );
}

// ── "what this is" ───────────────────────────────────────────────────────────
function WhatThisIs() {
  const steps = [
    ['1', 'Transcribe the source once', 'subgen transcribes your file in its own language. That transcript is the yardstick everything else is measured against.'],
    ['2', 'Run each recipe', 'Every recipe you choose runs as its own pass on the live model, producing one subtitle each.'],
    ['3', 'The judge ranks them', "subarr's tournament judge scores each result (invented lines, repetition, readability, and how faithful it is to the source) and crowns a winner."],
  ];
  return (
    <Collapsible label="What this is" defaultOpen>
      <p style={{ margin: '0 0 14px', color: 'var(--fg-1)', lineHeight: 1.6, fontSize: 'var(--text-base)' }}>
        Stop guessing which Whisper settings give the best subtitles for tricky content. Pick one file,
        choose a few recipes to compare, and let the judge tell you which one actually wins — objectively,
        on your own hardware and model.
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 12 }}>
        {steps.map(([n, title, body]) => (
          <div key={n} style={{ background: 'var(--bg-2)', border: 'var(--border)', borderRadius: 'var(--radius-lg)', padding: 14, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <span style={stepNum}>{n}</span><span style={{ fontWeight: 600, fontSize: 'var(--text-base)', color: 'var(--fg-0)' }}>{title}</span>
            </div>
            <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)', lineHeight: 1.55 }}>{body}</div>
          </div>
        ))}
      </div>
      <div style={expectStyle}>
        <b style={{ color: 'var(--fg-1)' }}>What to expect:</b> every recipe is a full transcription pass on your GPU, plus one
        pass to transcribe the source for comparison. So comparing three recipes means four passes — a sweep usually takes a few
        minutes. Progress shows live below, you can leave the page and come back, and nothing is written to your library.
      </div>
    </Collapsible>
  );
}

// ── cheat-sheet ──────────────────────────────────────────────────────────────
function KnobReference() {
  return (
    <Collapsible label="What the settings mean" defaultOpen>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {KNOBS.map(([name, type, desc]) => (
          <div key={name} style={{ fontSize: 'var(--text-base)', lineHeight: 1.5 }}>
            <code style={codeName}>{name}</code>
            <span style={{ color: 'var(--fg-3)', marginLeft: 8, fontSize: 'var(--text-sm)' }}>{type}</span>
            <div style={{ color: 'var(--fg-2)', marginTop: 2, fontSize: 'var(--text-sm)' }}>{desc}</div>
          </div>
        ))}
        <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-3)' }}>
          The curated recipes below are ready-made combinations of these — just choose the ones you want to compare.
        </div>
      </div>
    </Collapsible>
  );
}

// ── configure sweep ──────────────────────────────────────────────────────────
function SweepForm({ onRun, disabled, gate }) {
  const [mediaPath, setMediaPath] = useState('');
  const [picking, setPicking] = useState(false);
  const [sourceLang, setSourceLang] = useState('');
  const [selected, setSelected] = useState(() => new Set(DEFAULT_SELECTED));
  const [custom, setCustom] = useState([]);

  const toggle = (id) => setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const addCustom = () => setCustom((c) => [...c, { label: `custom-${c.length + 1}`, kwargs: '{}' }]);
  const setCustomRow = (i, patch) => setCustom((c) => c.map((v, j) => (j === i ? { ...v, ...patch } : v)));
  const removeCustom = (i) => setCustom((c) => c.filter((_, j) => j !== i));

  const customParsed = custom.map((v) => parseKwargs(v.kwargs));
  const chosen = CURATED.filter((c) => selected.has(c.id));
  const allLabels = [...chosen.map((c) => c.label), ...custom.map((c) => c.label.trim())];
  const dupLabel = allLabels.some((l, i) => l && allLabels.indexOf(l) !== i);
  const badCustom = customParsed.some((p) => !p.ok) || custom.some((c) => !c.label.trim());
  const total = chosen.length + custom.length;
  const ready = mediaPath.trim() && total >= 1 && !dupLabel && !badCustom;

  const submit = () => {
    if (!ready) return;
    const variants = [
      ...chosen.map((c) => ({ label: c.label, kwargs: c.kwargs })),
      ...custom.map((c, i) => ({ label: c.label.trim(), kwargs: customParsed[i].value })),
    ];
    onRun({ media_path: mediaPath.trim().replace(/^\/+/, ''), source_language: sourceLang || null, variants });
  };

  return (
    <SectionCard label="Configure sweep">
      {picking && <FilePicker onClose={() => setPicking(false)} onPick={(p) => { setMediaPath(p); setPicking(false); }} />}

      {/* media file */}
      <div style={fieldStyle}>
        <span style={lblStyle}>Media file</span>
        <div style={{ display: 'flex', gap: 8 }}>
          <input value={mediaPath} onChange={(e) => setMediaPath(e.target.value)} placeholder="Pick a file from your library…" style={{ ...inputStyle, flex: 1, minWidth: 0 }} />
          <button onClick={() => setPicking(true)} style={ghostBtnStyle}>Browse…</button>
        </div>
      </div>

      {/* spoken language */}
      <div style={fieldStyle}>
        <span style={lblStyle}>Spoken language</span>
        <select value={sourceLang} onChange={(e) => setSourceLang(e.target.value)} style={{ ...inputStyle, maxWidth: 260, cursor: 'pointer' }}>
          <option value="">Auto-detect</option>
          {LANGS.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
        </select>
        <Hint>Leave on Auto-detect and Whisper figures it out. Choosing the language when you know it nudges accuracy and saves a step.</Hint>
      </div>

      {/* recipes */}
      <div style={fieldStyle}>
        <span style={lblStyle}>Recipes to compare</span>
        <Hint>Choose the curated recipes you want to trial. Add your own at the bottom to test a specific tweak.</Hint>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 12, marginTop: 10 }}>
          {CURATED.map((c) => {
            const on = selected.has(c.id);
            return (
              <button key={c.id} onClick={() => toggle(c.id)} style={{
                textAlign: 'left', cursor: 'pointer', borderRadius: 'var(--radius-lg)', padding: 14, minWidth: 0,
                background: on ? 'rgba(52,211,153,0.08)' : 'var(--bg-2)',
                border: on ? '1px solid var(--success-500)' : 'var(--border)',
                transition: 'border-color var(--dur-base), background var(--dur-base)',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                  <span style={{
                    width: 18, height: 18, borderRadius: 'var(--radius-md)', flex: 'none',
                    border: on ? '1px solid var(--success-500)' : '1px solid var(--bg-5)',
                    background: on ? 'var(--success-500)' : 'transparent', color: 'var(--bg-0)',
                    fontSize: 12, fontWeight: 700, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                  }}>{on ? '✓' : ''}</span>
                  <span style={{ fontWeight: 600, fontSize: 'var(--text-md)', color: 'var(--fg-0)' }}>{c.label}</span>
                </div>
                <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)', lineHeight: 1.5 }}>{c.why}</div>
                <code style={{ display: 'block', marginTop: 8, fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)', color: 'var(--fg-3)', wordBreak: 'break-all', overflowWrap: 'anywhere' }}>
                  {JSON.stringify(c.kwargs)}
                </code>
              </button>
            );
          })}
        </div>

        {custom.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 12 }}>
            {custom.map((v, i) => (
              <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                <input value={v.label} onChange={(e) => setCustomRow(i, { label: e.target.value })} placeholder="label" style={{ ...inputStyle, width: 160, flex: 'none' }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <textarea value={v.kwargs} onChange={(e) => setCustomRow(i, { kwargs: e.target.value })} placeholder='{"beam_size": 5}' rows={1}
                            style={{ ...inputStyle, width: '100%', fontFamily: 'var(--font-mono)', resize: 'vertical', minHeight: 36, border: customParsed[i].ok ? 'var(--border)' : '1px solid var(--error-500)' }} />
                  {!customParsed[i].ok && <div style={{ color: 'var(--error-500)', fontSize: 'var(--text-sm)', marginTop: 2 }}>kwargs {customParsed[i].error}</div>}
                </div>
                <button onClick={() => removeCustom(i)} title="remove" style={iconBtnStyle}><Glyph char={ICONS.close || '×'} size={14} /></button>
              </div>
            ))}
          </div>
        )}
        <button onClick={addCustom} style={{ ...ghostBtnStyle, marginTop: 10 }}>+ Add custom recipe</button>
        {dupLabel && <div style={{ color: 'var(--error-500)', fontSize: 'var(--text-sm)', marginTop: 6 }}>Recipe names must be unique.</div>}
        {total === 0 && <div style={{ color: 'var(--fg-3)', fontSize: 'var(--text-sm)', marginTop: 6 }}>Choose at least one recipe to compare.</div>}
      </div>

      {gate && !gate.ok && (
        <div style={gateNoticeStyle}>{gate.reason}{gate.remedy && <div style={{ marginTop: 4, color: 'var(--fg-3)' }}>{gate.remedy}</div>}</div>
      )}

      <div style={{ borderTop: 'var(--border)', paddingTop: 16, marginTop: 2 }}>
        <button onClick={submit} disabled={!ready || disabled} style={{ ...primaryBtnStyle, opacity: (!ready || disabled) ? 0.5 : 1, cursor: (!ready || disabled) ? 'not-allowed' : 'pointer' }}>
          {disabled ? 'Sweep running…' : `Run sweep${total ? ` · ${total} recipe${total === 1 ? '' : 's'}` : ''}`}
        </button>
      </div>
    </SectionCard>
  );
}

// ── results ──────────────────────────────────────────────────────────────────
const COL_HELP = {
  Overall: 'The judge’s overall score, 0–100. Higher is better. This decides the winner.',
  Faithfulness: 'How close the meaning is to the source (0–1, higher is closer). Blank if the optional accuracy model isn’t installed — ranking then uses readability and clean-output checks only.',
  Readability: 'How cleanly it reads: timing, line length, lines per second. 0–100.',
  Lines: 'How many subtitle lines it produced.',
};

function ResultPanel({ run }) {
  if (!run) {
    return (
      <SectionCard label="Results">
        <div style={{ color: 'var(--fg-2)', fontSize: 'var(--text-base)', lineHeight: 1.6 }}>
          Nothing run yet. Set up a sweep above and press <b>Run sweep</b>. When it finishes you’ll get a ranked table here —
          the <b>★ winner</b> is the recipe the judge rated best. Copy its settings into your subgen config to make it your default.
        </div>
      </SectionCard>
    );
  }
  const scorecards = run.result?.scorecards || [];
  const winner = run.result?.winner_label;
  const winnerKwargs = (run.variants || []).find((v) => v.label === winner)?.kwargs;

  return (
    <SectionCard label="Results" action={<StatusPill status={run.status} />}>
      {run.error && <div style={{ color: 'var(--error-500)', marginBottom: 12 }}>Error: {run.error}</div>}

      {run.status === 'running' && (
        <div style={{ marginBottom: 14, fontSize: 'var(--text-base)', color: 'var(--fg-2)' }}>
          <div>Source transcript: {run.source_text != null ? '✓ ready' : 'transcribing…'}</div>
          <div style={{ marginTop: 4 }}>Recipes: {run.outcomes.length} of {run.variants.length} done</div>
          <div style={{ marginTop: 4, color: 'var(--fg-3)', fontSize: 'var(--text-sm)' }}>Each recipe is a full transcription pass — hang tight.</div>
        </div>
      )}

      {scorecards.length > 0 ? (
        <>
          {winner && (
            <div style={winnerBoxStyle}>
              <div style={{ fontWeight: 700, marginBottom: 4, color: 'var(--fg-0)' }}>★ Winner: {winner}</div>
              <div style={{ fontSize: 'var(--text-base)', color: 'var(--fg-2)' }}>Make it your default by adding this to your subgen config:</div>
              <code style={{ display: 'block', marginTop: 6, fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)', background: 'var(--bg-0)', border: 'var(--border)', padding: '8px 10px', borderRadius: 'var(--radius-lg)', overflowX: 'auto', color: 'var(--fg-1)' }}>
                SUBGEN_KWARGS={JSON.stringify(winnerKwargs || {})}
              </code>
              <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)', marginTop: 6 }}>A one-click “adopt winner” is coming — for now, copy and paste.</div>
            </div>
          )}
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--text-base)' }}>
              <thead>
                <tr style={{ textAlign: 'left', color: 'var(--fg-3)' }}>
                  <th style={thStyle}>#</th><th style={thStyle}>Recipe</th>
                  {['Overall', 'Faithfulness', 'Readability', 'Lines'].map((c) => <th key={c} style={thStyle} title={COL_HELP[c]}>{c}</th>)}
                </tr>
              </thead>
              <tbody>
                {scorecards.map((sc, i) => {
                  const isWin = sc.entrant_label === winner;
                  return (
                    <tr key={sc.entrant_label} style={{ borderTop: 'var(--border)', background: isWin ? 'rgba(52,211,153,0.08)' : undefined }}>
                      <td style={tdStyle}>{i + 1}</td>
                      <td style={{ ...tdStyle, fontWeight: isWin ? 700 : 500, color: 'var(--fg-0)' }}>
                        {isWin && <span title="winner" style={{ marginRight: 6 }}>★</span>}{sc.entrant_label}
                        {sc.disqualified && <span title="produced unusable output" style={{ color: 'var(--error-500)', marginLeft: 6, fontSize: 'var(--text-xs)' }}>unusable</span>}
                      </td>
                      <td style={tdStyle}>{sc.composite?.toFixed(1)}</td>
                      <td style={tdStyle}>{sc.qe_adequacy != null ? sc.qe_adequacy.toFixed(3) : '—'}</td>
                      <td style={tdStyle}>{sc.readability_score?.toFixed(0)}</td>
                      <td style={tdStyle}>{sc.cue_count}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)', marginTop: 8, lineHeight: 1.5 }}>
            Hover a column heading for what it means. Ranked best-first by Overall; faithfulness counts heavily when the accuracy model is available.
          </div>
        </>
      ) : run.status === 'done' ? (
        <div style={{ color: 'var(--fg-2)' }}>No usable results — every recipe was rejected or produced no subtitle. Try a clip with clearer dialogue, or set the spoken language.</div>
      ) : null}

      {run.status === 'done' && run.source_text == null && (
        <div style={{ marginTop: 10, fontSize: 'var(--text-sm)', color: 'var(--fg-3)' }}>No source transcript was produced, so faithfulness was skipped — ranking used readability and clean-output checks only.</div>
      )}
    </SectionCard>
  );
}

export function ArenaPage() {
  const [run, setRun] = useState(null);
  const [busy, setBusy] = useState(false);
  const [gate, setGate] = useState(null);
  const esRef = useRef(null);

  useEffect(() => {
    let alive = true;
    fetch('/api/integrations/health').then((r) => r.json()).then((d) => {
      if (!alive) return;
      const ok = !!d?.subgen?.asr_arena;
      setGate(ok ? { ok: true } : { ok: false, reason: 'This subgen build can’t run the tuning lab yet (it needs subarr-subgen v4.10 or newer).', remedy: 'Upgrade your subgen image to ghcr.io/coaxk/subarr-subgen:latest.' });
    }).catch(() => {});
    return () => { alive = false; };
  }, []);

  const closeStream = useCallback(() => { if (esRef.current) { esRef.current.close(); esRef.current = null; } }, []);
  const refresh = useCallback((id) => {
    fetch(`/api/arena/${id}`).then((r) => r.json()).then((d) => { setRun(d); if (d.status === 'done' || d.status === 'error') { setBusy(false); closeStream(); } }).catch(() => {});
  }, [closeStream]);

  const onRun = useCallback(async (body) => {
    setBusy(true); setRun(null);
    try {
      const r = await fetch('/api/arena/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const data = await r.json();
      if (!r.ok) {
        const detail = data?.detail;
        setRun({ status: 'error', error: typeof detail === 'string' ? detail : (detail?.reason || 'request failed'), variants: body.variants, outcomes: [] });
        setBusy(false); return;
      }
      setRun(data);
      closeStream();
      const es = new EventSource(`/api/arena/${data.id}/events`);
      esRef.current = es;
      const onEvt = () => refresh(data.id);
      ['start', 'source', 'variant', 'done', 'error'].forEach((ev) => es.addEventListener(ev, onEvt));
      es.onerror = () => { es.close(); };
    } catch (e) {
      setRun({ status: 'error', error: String(e), variants: body.variants, outcomes: [] });
      setBusy(false);
    }
  }, [closeStream, refresh]);

  useEffect(() => closeStream, [closeStream]);

  return (
    <main className="main-canvas" style={{ padding: '22px 24px 22px', gap: 14, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
      <div>
        <h1 style={{ margin: 0, fontSize: 'var(--text-h1)', fontWeight: 600 }}>Tuning Lab</h1>
        <div style={{ marginTop: 4, fontSize: 'var(--text-sm)', color: 'var(--fg-2)', maxWidth: 720 }}>
          Find the Whisper settings that give the best subtitles for a file, by trialling a few against the live model and
          letting the tournament judge rank them. Runs over subgen’s transcription engine — no upload, nothing written to your library.
        </div>
      </div>
      <WhatThisIs />
      <KnobReference />
      <SweepForm onRun={onRun} disabled={busy} gate={gate} />
      <ResultPanel run={run} />
    </main>
  );
}

// ── styles (home-hifi tokens) ────────────────────────────────────────────────
const cardStyle = { background: 'var(--bg-1)', border: 'var(--border)', borderRadius: 'var(--radius-lg)', padding: '16px 18px' };
const fieldStyle = { display: 'flex', flexDirection: 'column', gap: 6 };
const lblStyle = { fontSize: 'var(--text-base)', fontWeight: 600, color: 'var(--fg-1)' };
const inputStyle = { background: 'var(--bg-2)', border: 'var(--border)', borderRadius: 'var(--radius-md)', padding: '8px 10px', color: 'var(--fg-0)', fontSize: 'var(--text-md)', outline: 'none' };
const thStyle = { padding: '6px 8px', fontWeight: 600, fontSize: 'var(--text-sm)', cursor: 'help', whiteSpace: 'nowrap' };
const tdStyle = { padding: '8px 8px', color: 'var(--fg-1)' };
const stepNum = { width: 22, height: 22, borderRadius: 'var(--radius-pill)', background: 'var(--violet-500)', color: '#fff', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 'var(--text-sm)', fontWeight: 700, flex: 'none' };
const expectStyle = { marginTop: 14, fontSize: 'var(--text-sm)', color: 'var(--fg-2)', lineHeight: 1.6, background: 'var(--bg-2)', border: 'var(--border)', borderRadius: 'var(--radius-lg)', padding: '10px 12px' };
const primaryBtnStyle = { background: 'var(--success-500)', color: 'var(--bg-0)', border: 'none', borderRadius: 'var(--radius-md)', padding: '10px 18px', fontWeight: 700, fontSize: 'var(--text-md)' };
const ghostBtnStyle = { background: 'var(--bg-2)', color: 'var(--fg-1)', border: 'var(--border)', borderRadius: 'var(--radius-md)', padding: '8px 14px', fontSize: 'var(--text-base)', cursor: 'pointer', flex: 'none' };
const iconBtnStyle = { background: 'var(--bg-2)', color: 'var(--fg-2)', border: 'var(--border)', borderRadius: 'var(--radius-md)', padding: '8px 10px', cursor: 'pointer', flex: 'none' };
const crumbStyle = { background: 'transparent', border: 'none', color: 'var(--violet-400)', cursor: 'pointer', fontSize: 'var(--text-sm)', padding: 0 };
const codeName = { color: 'var(--violet-400)', fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)' };
const gateNoticeStyle = { background: 'rgba(245,158,11,0.10)', border: '1px solid var(--warn-500)', borderRadius: 'var(--radius-lg)', padding: '10px 12px', fontSize: 'var(--text-base)', color: 'var(--fg-1)' };
const winnerBoxStyle = { background: 'rgba(52,211,153,0.08)', border: '1px solid var(--success-500)', borderRadius: 'var(--radius-lg)', padding: '12px 14px', marginBottom: 14 };
const modalBackdrop = { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 };
const modalCard = { background: 'var(--bg-1)', border: 'var(--border-strong)', borderRadius: 'var(--radius-lg)', padding: 18, width: 'min(640px, 92vw)', boxShadow: 'var(--shadow-modal)' };
const pickRow = { display: 'block', width: '100%', textAlign: 'left', background: 'transparent', border: 'none', borderBottom: 'var(--border)', color: 'var(--fg-1)', padding: '10px 12px', cursor: 'pointer', fontSize: 'var(--text-base)' };
const pickRowMuted = { padding: '16px 12px', color: 'var(--fg-3)', fontSize: 'var(--text-base)' };
