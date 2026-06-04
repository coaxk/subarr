// #131 — Tuning Lab: drive a subgen config sweep against the live model and
// rank the outputs with the validated tournament judge.
//
// Flow: pick a media file + choose configs to compare (curated presets and/or
// custom kwargs) → POST /api/arena/run → stream /events (SSE) for live
// progress → render the ranked scorecard. Rides subgen's v4.10 /asr path-input
// channel, so there's no upload and no shared scratch.
//
// Heavy on explanation by design — most people have never tuned Whisper kwargs
// and shouldn't have to read the docs to get value here.

import { SectionCard, StatusDot, Glyph, ICONS } from './atoms.jsx';

const { useState, useEffect, useRef, useCallback } = React;

// Curated, purpose-built configs the user toggles into a sweep. Each is a
// named recipe with a plain-English reason, so nobody has to hand-write JSON
// unless they want to. (This is the seed of the eventual crowd-curated set.)
const CURATED = [
  { id: 'default', label: 'default', kwargs: {}, why: 'Your current subgen settings, unchanged. The baseline every other config has to beat.' },
  { id: 'noisy', label: 'noisy-robust', kwargs: { vad_filter: true, beam_size: 5, temperature: 0 }, why: 'Skips non-speech and searches harder. Best for music/effects-heavy or low-quality audio.' },
  { id: 'anti-repeat', label: 'anti-repeat', kwargs: { condition_on_previous_text: false, compression_ratio_threshold: 2.4 }, why: 'Stops the model parroting the previous line. Reach for it when output loops or drifts.' },
  { id: 'accurate', label: 'high-accuracy', kwargs: { beam_size: 8, best_of: 5, temperature: 0 }, why: 'Widest search. Slowest of the bunch, but squeezes out the most accurate read.' },
  { id: 'halluc-guard', label: 'hallucination-guard', kwargs: { vad_filter: true, condition_on_previous_text: false, no_speech_threshold: 0.6 }, why: 'Aggressive about silence with no carry-over. For sparse dialogue or long quiet stretches.' },
  { id: 'fast', label: 'fast-draft', kwargs: { beam_size: 1 }, why: 'Quick single pass. Good for a fast sanity check, not for final quality.' },
];
const DEFAULT_SELECTED = ['default', 'noisy', 'anti-repeat'];

// Plain-language cheat sheet for the most useful Whisper knobs.
const KNOBS = [
  ['beam_size', 'int (1–10)', 'How hard it searches for the best transcription. Higher is more accurate but slower. ~5 is a sensible max.'],
  ['vad_filter', 'true / false', 'Drops silence and non-speech before transcribing. The single biggest win against hallucinated lines over music/silence.'],
  ['temperature', '0.0–1.0', 'Randomness. 0 is deterministic and usually best; the model only climbs higher as a fallback.'],
  ['condition_on_previous_text', 'true / false', 'Whether each line is influenced by the last. Set false when output repeats or drifts.'],
  ['compression_ratio_threshold', 'float (~2.4)', 'Flags gibberish/looping. Lower is stricter about rejecting junk segments.'],
  ['initial_prompt', '"text"', 'Primes vocabulary and style (names, jargon, tone). Handy for niche content.'],
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
  return <div style={{ fontSize: 12, color: 'var(--text-3)', marginTop: 4, lineHeight: 1.45 }}>{children}</div>;
}

function StatusPill({ status }) {
  const map = {
    pending: { kind: 'idle', text: 'Pending' }, running: { kind: 'busy', text: 'Running' },
    done: { kind: 'ok', text: 'Done' }, error: { kind: 'err', text: 'Error' },
  };
  const s = map[status] || map.pending;
  return <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13 }}><StatusDot kind={s.kind} pulse={status === 'running'} />{s.text}</span>;
}

// ── file picker modal (browse the library, don't type paths) ─────────────────
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
      .then((r) => r.json())
      .then((d) => { if (alive) setEntries(d.entries || []); })
      .catch((e) => { if (alive) setErr(String(e)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [path]);

  const parts = path ? path.split('/') : [];
  const dirs = (entries || []).filter((e) => e.is_dir);
  const files = (entries || []).filter((e) => !e.is_dir && VIDEO_RE.test(e.name));

  return (
    <div style={modalBackdrop} onClick={onClose}>
      <div style={modalCard} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
          <strong style={{ fontSize: 15 }}>Pick a media file</strong>
          <button onClick={onClose} style={iconBtnStyle}><Glyph char={ICONS.close || '×'} size={14} /></button>
        </div>
        {/* breadcrumb */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, fontSize: 13, marginBottom: 10 }}>
          <button onClick={() => setPath('')} style={crumbStyle}>library</button>
          {parts.map((p, i) => (
            <span key={i} style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
              <span style={{ color: 'var(--text-3)' }}>/</span>
              <button onClick={() => setPath(parts.slice(0, i + 1).join('/'))} style={crumbStyle}>{p}</button>
            </span>
          ))}
        </div>
        <div style={{ minHeight: 200, maxHeight: 420, overflow: 'auto', border: '1px solid var(--border-0)', borderRadius: 8 }}>
          {loading ? <div style={pickRowMuted}>Loading…</div>
            : err ? <div style={{ ...pickRowMuted, color: 'var(--danger)' }}>Couldn’t browse: {err}</div>
            : (dirs.length === 0 && files.length === 0) ? <div style={pickRowMuted}>Empty folder.</div>
            : (
              <>
                {dirs.map((e) => (
                  <button key={e.path} onClick={() => setPath(e.path)} style={pickRow}>
                    <span style={{ marginRight: 8 }}>📁</span>{e.name}
                    {e.video_count ? <span style={{ color: 'var(--text-3)', marginLeft: 8, fontSize: 12 }}>{e.video_count} video{e.video_count === 1 ? '' : 's'}</span> : null}
                  </button>
                ))}
                {files.map((e) => (
                  <button key={e.path} onClick={() => onPick(e.path)} style={{ ...pickRow, color: 'var(--text-0)' }}>
                    <span style={{ marginRight: 8 }}>🎬</span>{e.name}
                  </button>
                ))}
              </>
            )}
        </div>
        <Hint>Tip: a short, representative clip (a few minutes with real dialogue) compares configs just as well as a full episode and finishes far faster.</Hint>
      </div>
    </div>
  );
}

// ── "what this is" — what it does + what to expect, up front ─────────────────
function HowItWorks() {
  const steps = [
    ['1', 'Transcribe the source once', 'subgen transcribes your file in its own language. That transcript is the reference everything is measured against.'],
    ['2', 'Run each config', 'Every config you pick runs as its own pass on the live model, producing one subtitle each.'],
    ['3', 'The judge ranks them', "subarr's tournament judge scores each output (hallucinations, repetition, readability, adequacy vs the reference) and crowns a winner."],
  ];
  return (
    <SectionCard label="What this is">
      <p style={{ margin: '0 0 14px', color: 'var(--text-2)', lineHeight: 1.55 }}>
        Stop guessing which Whisper settings give the best subtitles for tricky content. Pick one file,
        choose a few configs to compare, and let the judge tell you which one actually wins — objectively,
        on your own hardware and model.
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
        {steps.map(([n, title, body]) => (
          <div key={n} style={{ background: 'var(--bg-1)', border: '1px solid var(--border-0)', borderRadius: 10, padding: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <span style={stepNum}>{n}</span><span style={{ fontWeight: 600, fontSize: 13 }}>{title}</span>
            </div>
            <div style={{ fontSize: 12.5, color: 'var(--text-3)', lineHeight: 1.5 }}>{body}</div>
          </div>
        ))}
      </div>
      <div style={expectStyle}>
        <b>What to expect:</b> each config is a full transcription pass on your GPU, so a sweep of N configs takes
        roughly as long as transcribing the clip N+1 times (the +1 is the source reference). Progress streams live,
        and you can leave the page and come back. Nothing is written to your library.
      </div>
    </SectionCard>
  );
}

// ── always-on cheat-sheet, above the config picker ───────────────────────────
function KnobReference() {
  return (
    <SectionCard label="Common settings, in plain English">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {KNOBS.map(([name, type, desc]) => (
          <div key={name} style={{ fontSize: 12.5, lineHeight: 1.5 }}>
            <code style={{ color: 'var(--violet-300, #c4b5fd)', fontFamily: 'var(--font-mono)' }}>{name}</code>
            <span style={{ color: 'var(--text-3)', marginLeft: 8 }}>{type}</span>
            <div style={{ color: 'var(--text-2)', marginTop: 2 }}>{desc}</div>
          </div>
        ))}
        <div style={{ fontSize: 12, color: 'var(--text-3)' }}>
          A config is just a JSON object of these. Empty <code>{'{}'}</code> means "use my current subgen settings".
          The curated configs below are pre-built combinations — tick the ones you want to compare.
        </div>
      </div>
    </SectionCard>
  );
}

// ── the config-sweep form ────────────────────────────────────────────────────
function SweepForm({ onRun, disabled, gate }) {
  const [mediaPath, setMediaPath] = useState('');
  const [picking, setPicking] = useState(false);
  const [sourceLang, setSourceLang] = useState('');
  const [selected, setSelected] = useState(() => new Set(DEFAULT_SELECTED));
  const [custom, setCustom] = useState([]); // [{label, kwargs}]

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
    onRun({ media_path: mediaPath.trim().replace(/^\/+/, ''), source_language: sourceLang.trim() || null, variants });
  };

  return (
    <SectionCard label="Configure sweep">
      {picking && <FilePicker onClose={() => setPicking(false)} onPick={(p) => { setMediaPath(p); setPicking(false); }} />}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        {/* media file with browse */}
        <div style={fieldStyle}>
          <span style={lblStyle}>Media file</span>
          <div style={{ display: 'flex', gap: 8 }}>
            <input value={mediaPath} onChange={(e) => setMediaPath(e.target.value)} placeholder="Pick a file from your library…" style={{ ...inputStyle, flex: 1 }} />
            <button onClick={() => setPicking(true)} style={ghostBtnStyle}>Browse…</button>
          </div>
        </div>

        {/* source language */}
        <div style={fieldStyle}>
          <span style={lblStyle}>Source language <span style={{ color: 'var(--text-3)', fontWeight: 400 }}>· optional</span></span>
          <input value={sourceLang} onChange={(e) => setSourceLang(e.target.value)} placeholder="auto-detect" style={{ ...inputStyle, maxWidth: 220 }} />
          <Hint>Leave blank to let Whisper detect it. Set an ISO code (e.g. <code>ko</code>, <code>ja</code>, <code>fr</code>) if you already know it — it nudges accuracy and skips a step.</Hint>
        </div>

        {/* curated config picker */}
        <div>
          <span style={lblStyle}>Configs to compare</span>
          <Hint>Tick the curated recipes you want to trial. Add your own below if you want to test a specific tweak.</Hint>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 10, marginTop: 10 }}>
            {CURATED.map((c) => {
              const on = selected.has(c.id);
              return (
                <button key={c.id} onClick={() => toggle(c.id)} style={{
                  textAlign: 'left', cursor: 'pointer', borderRadius: 10, padding: 12,
                  background: on ? 'var(--accent-soft, rgba(34,211,161,0.08))' : 'var(--bg-1)',
                  border: `1px solid ${on ? 'var(--accent, #22d3a1)' : 'var(--border-0)'}`,
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                    <span style={{ width: 16, height: 16, borderRadius: 4, border: `1px solid ${on ? 'var(--accent)' : 'var(--border-0)'}`,
                                   background: on ? 'var(--accent)' : 'transparent', color: 'var(--bg-0)', fontSize: 11,
                                   display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: 'none' }}>{on ? '✓' : ''}</span>
                    <span style={{ fontWeight: 600, fontSize: 13 }}>{c.label}</span>
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--text-3)', lineHeight: 1.45 }}>{c.why}</div>
                  <code style={{ display: 'block', marginTop: 6, fontSize: 11.5, fontFamily: 'var(--font-mono)', color: 'var(--text-2)' }}>
                    {JSON.stringify(c.kwargs)}
                  </code>
                </button>
              );
            })}
          </div>

          {/* custom rows */}
          {custom.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 12 }}>
              {custom.map((v, i) => (
                <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                  <input value={v.label} onChange={(e) => setCustomRow(i, { label: e.target.value })} placeholder="label" style={{ ...inputStyle, width: 160, flex: 'none' }} />
                  <div style={{ flex: 1 }}>
                    <textarea value={v.kwargs} onChange={(e) => setCustomRow(i, { kwargs: e.target.value })} placeholder='{"beam_size": 5}' rows={1}
                              style={{ ...inputStyle, width: '100%', fontFamily: 'var(--font-mono)', resize: 'vertical', minHeight: 34, borderColor: customParsed[i].ok ? undefined : 'var(--danger)' }} />
                    {!customParsed[i].ok && <div style={{ color: 'var(--danger)', fontSize: 12, marginTop: 2 }}>kwargs {customParsed[i].error}</div>}
                  </div>
                  <button onClick={() => removeCustom(i)} title="remove" style={iconBtnStyle}><Glyph char={ICONS.close || '×'} size={14} /></button>
                </div>
              ))}
            </div>
          )}
          <button onClick={addCustom} style={{ ...ghostBtnStyle, marginTop: 10 }}>+ Add custom config</button>
          {dupLabel && <div style={{ color: 'var(--danger)', fontSize: 12, marginTop: 6 }}>Config labels must be unique.</div>}
          {total === 0 && <div style={{ color: 'var(--text-3)', fontSize: 12, marginTop: 6 }}>Pick at least one config to compare.</div>}
        </div>

        {gate && !gate.ok && (
          <div style={gateNoticeStyle}>{gate.reason}{gate.remedy && <div style={{ marginTop: 4, color: 'var(--text-3)' }}>{gate.remedy}</div>}</div>
        )}

        <div style={{ borderTop: '1px solid var(--border-0)', paddingTop: 14 }}>
          <button onClick={submit} disabled={!ready || disabled} style={primaryBtnStyle}>
            {disabled ? 'Sweep running…' : `Run sweep · ${total} config${total === 1 ? '' : 's'}`}
          </button>
        </div>
      </div>
    </SectionCard>
  );
}

// ── live progress + ranked result ────────────────────────────────────────────
const COL_HELP = {
  Composite: 'Overall judge score, 0–100. Higher is better. This is what decides the winner.',
  QE: 'Adequacy vs the source transcript (0–1, higher = closer in meaning). Blank if the QE model isn’t installed — ranking then uses structural quality only.',
  Readability: 'How clean the subtitle reads (timing, line length, cues per second). 0–100.',
  Cues: 'Number of subtitle lines produced.',
};

function ResultPanel({ run }) {
  if (!run) {
    return (
      <SectionCard label="Results">
        <div style={{ color: 'var(--text-3)', fontSize: 13, lineHeight: 1.6 }}>
          Nothing run yet. Configure a sweep above and hit <b>Run sweep</b>. When it finishes you'll get a ranked table here:
          the <b>★ winner</b> is the config the judge rated best. Take its <code>kwargs</code> and drop them into your subgen
          settings (<code>SUBGEN_KWARGS</code>, or <code>SUBGEN_KWARGS_LANG_&lt;code&gt;</code> for that language) to make it your default.
        </div>
      </SectionCard>
    );
  }
  const scorecards = run.result?.scorecards || [];
  const winner = run.result?.winner_label;
  const winnerKwargs = (run.variants || []).find((v) => v.label === winner)?.kwargs;

  return (
    <SectionCard label="Results" action={<StatusPill status={run.status} />}>
      {run.error && <div style={{ color: 'var(--danger)', marginBottom: 12 }}>Error: {run.error}</div>}

      {run.status === 'running' && (
        <div style={{ marginBottom: 14, fontSize: 13, color: 'var(--text-2)' }}>
          <div>Source transcript: {run.source_text != null ? '✓ ready' : 'transcribing…'}</div>
          <div style={{ marginTop: 4 }}>Configs: {run.outcomes.length} / {run.variants.length} processed</div>
          <div style={{ marginTop: 4, color: 'var(--text-3)', fontSize: 12 }}>Each config is a full transcription pass — hang tight.</div>
        </div>
      )}

      {scorecards.length > 0 ? (
        <>
          {winner && (
            <div style={winnerBoxStyle}>
              <div style={{ fontWeight: 700, marginBottom: 4 }}>★ Winner: {winner}</div>
              <div style={{ fontSize: 13, color: 'var(--text-2)' }}>Make it your default by putting these in your subgen config:</div>
              <code style={{ display: 'block', marginTop: 6, fontFamily: 'var(--font-mono)', fontSize: 12.5, background: 'var(--bg-0)', padding: '8px 10px', borderRadius: 6, overflowX: 'auto' }}>
                SUBGEN_KWARGS={JSON.stringify(winnerKwargs || {})}
              </code>
              <div style={{ fontSize: 11.5, color: 'var(--text-3)', marginTop: 6 }}>(A one-click "adopt winner" is coming — for now, copy + paste.)</div>
            </div>
          )}
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ textAlign: 'left', color: 'var(--text-3)' }}>
                <th style={thStyle}>#</th><th style={thStyle}>Config</th>
                {['Composite', 'QE', 'Readability', 'Cues'].map((c) => <th key={c} style={thStyle} title={COL_HELP[c]}>{c} <span style={{ color: 'var(--text-3)' }}>ⓘ</span></th>)}
              </tr>
            </thead>
            <tbody>
              {scorecards.map((sc, i) => {
                const isWin = sc.entrant_label === winner;
                return (
                  <tr key={sc.entrant_label} style={{ borderTop: '1px solid var(--border-0)', background: isWin ? 'var(--accent-soft, rgba(34,211,161,0.08))' : undefined }}>
                    <td style={tdStyle}>{i + 1}</td>
                    <td style={{ ...tdStyle, fontWeight: isWin ? 700 : 500 }}>
                      {isWin && <span title="winner" style={{ marginRight: 6 }}>★</span>}{sc.entrant_label}
                      {sc.disqualified && <span title="produced unusable output" style={{ color: 'var(--danger)', marginLeft: 6, fontSize: 11 }}>DQ</span>}
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
          <div style={{ fontSize: 11.5, color: 'var(--text-3)', marginTop: 8, lineHeight: 1.5 }}>
            Hover a column header for what it means. Ranked best-first by Composite; the judge weighs adequacy (QE) heavily when it's available.
          </div>
        </>
      ) : run.status === 'done' ? (
        <div style={{ color: 'var(--text-3)' }}>No scorable candidates — every config was disqualified or produced no subtitle. Try a clip with clearer dialogue, or set the source language explicitly.</div>
      ) : null}

      {run.status === 'done' && run.source_text == null && (
        <div style={{ marginTop: 10, fontSize: 12, color: 'var(--text-3)' }}>No source transcript was produced, so adequacy (QE) was skipped — ranking used structural quality only.</div>
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
      setGate(ok ? { ok: true } : { ok: false, reason: 'This subgen build does not support the tuning lab (needs subarr-subgen ≥ v4.10).', remedy: 'Upgrade to ghcr.io/coaxk/subarr-subgen:latest (≥ 2026.05.3-r4).' });
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
    <div className="main-canvas" style={{ overflow: 'auto', padding: 24 }}>
      <div style={{ maxWidth: 920, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 18 }}>
        <header>
          <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 24, margin: 0 }}>Tuning Lab</h1>
          <p style={{ color: 'var(--text-2)', marginTop: 6, maxWidth: 640 }}>
            Find the Whisper config that gives the best subtitles for a file, by trialling several against the
            live model and letting the tournament judge rank them. Runs over subgen's <code>/asr</code> channel —
            no upload, nothing written to your library.
          </p>
        </header>
        <HowItWorks />
        <KnobReference />
        <SweepForm onRun={onRun} disabled={busy} gate={gate} />
        <ResultPanel run={run} />
      </div>
    </div>
  );
}

// ── inline styles ────────────────────────────────────────────────────────────
const fieldStyle = { display: 'flex', flexDirection: 'column', gap: 6 };
const lblStyle = { fontSize: 13, fontWeight: 600, color: 'var(--text-1)' };
const inputStyle = { background: 'var(--bg-1)', border: '1px solid var(--border-0)', borderRadius: 8, padding: '8px 10px', color: 'var(--text-0)', fontSize: 14, outline: 'none' };
const thStyle = { padding: '6px 8px', fontWeight: 600, fontSize: 12, cursor: 'help' };
const tdStyle = { padding: '8px 8px' };
const stepNum = { width: 22, height: 22, borderRadius: 11, background: 'var(--violet-500)', color: '#fff', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 700, flex: 'none' };
const expectStyle = { marginTop: 14, fontSize: 12.5, color: 'var(--text-2)', lineHeight: 1.55, background: 'var(--bg-1)', border: '1px solid var(--border-0)', borderRadius: 8, padding: '10px 12px' };
const primaryBtnStyle = { background: 'var(--accent)', color: 'var(--bg-0)', border: 'none', borderRadius: 8, padding: '10px 18px', fontWeight: 600, fontSize: 14, cursor: 'pointer' };
const ghostBtnStyle = { background: 'transparent', color: 'var(--text-1)', border: '1px solid var(--border-0)', borderRadius: 8, padding: '8px 14px', fontSize: 13, cursor: 'pointer', flex: 'none' };
const iconBtnStyle = { background: 'transparent', color: 'var(--text-3)', border: '1px solid var(--border-0)', borderRadius: 8, padding: '8px 10px', cursor: 'pointer', flex: 'none' };
const crumbStyle = { background: 'transparent', border: 'none', color: 'var(--violet-300, #c4b5fd)', cursor: 'pointer', fontSize: 13, padding: 0 };
const gateNoticeStyle = { background: 'var(--warn-soft, rgba(245,158,11,0.10))', border: '1px solid var(--warn, #f59e0b)', borderRadius: 8, padding: '10px 12px', fontSize: 13, color: 'var(--text-1)' };
const winnerBoxStyle = { background: 'var(--accent-soft, rgba(34,211,161,0.08))', border: '1px solid var(--accent, #22d3a1)', borderRadius: 10, padding: '12px 14px', marginBottom: 14 };
const modalBackdrop = { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 };
const modalCard = { background: 'var(--bg-0)', border: '1px solid var(--border-0)', borderRadius: 12, padding: 18, width: 'min(620px, 92vw)', boxShadow: '0 20px 60px rgba(0,0,0,0.5)' };
const pickRow = { display: 'block', width: '100%', textAlign: 'left', background: 'transparent', border: 'none', borderBottom: '1px solid var(--border-0)', color: 'var(--text-1)', padding: '9px 12px', cursor: 'pointer', fontSize: 13.5 };
const pickRowMuted = { padding: '14px 12px', color: 'var(--text-3)', fontSize: 13 };
