// #131 — Tuning Lab: drive a subgen config sweep against the live model and
// rank the outputs with the validated tournament judge.
//
// Flow: pick a media file + define N config variants (each a per-request
// SUBGEN_KWARGS override) → POST /api/arena/run → stream /events (SSE) for
// live progress → render the ranked scorecard. The whole thing rides subgen's
// v4.10 /asr path-input channel, so there's no upload and no shared scratch.
//
// This page is deliberately heavy on explanation — most people have never
// tuned Whisper kwargs and shouldn't have to read the docs to get value here.

import { SectionCard, StatusDot, Glyph, ICONS } from './atoms.jsx';

const { useState, useEffect, useRef, useCallback } = React;

// Ready-made starting points, each with a one-line rationale shown in the UI.
const PRESETS = [
  { label: 'default', kwargs: '{}', why: 'Your subgen settings, unchanged. The baseline to beat.' },
  {
    label: 'noisy-robust',
    kwargs: '{"vad_filter": true, "beam_size": 5, "temperature": 0}',
    why: 'Skips non-speech and searches harder. Good for music/effects-heavy or low-quality audio.',
  },
  {
    label: 'anti-repeat',
    kwargs: '{"condition_on_previous_text": false, "compression_ratio_threshold": 2.4}',
    why: 'Stops the model parroting the previous line. Good when output loops or drifts.',
  },
];

// Plain-language cheat sheet for the most useful Whisper knobs.
const KNOBS = [
  ['beam_size', 'int (1–10)', 'How hard it searches for the best transcription. Higher is more accurate but slower. ~5 is a sensible max.'],
  ['vad_filter', 'true / false', 'Drops silence and non-speech before transcribing. The single biggest win against hallucinated lines over music/silence.'],
  ['temperature', '0.0–1.0', 'Randomness. 0 is deterministic and usually best; the model only climbs higher as a fallback.'],
  ['condition_on_previous_text', 'true / false', 'Whether each line is influenced by the last. Set false when output repeats or drifts.'],
  ['compression_ratio_threshold', 'float (~2.4)', 'Flags gibberish/looping. Lower is stricter about rejecting junk segments.'],
  ['initial_prompt', '"text"', 'Primes vocabulary and style (names, jargon, tone). Handy for niche content.'],
];

function emptyVariant(n) { return { label: `variant-${n}`, kwargs: '{}' }; }

function parseKwargs(text) {
  const t = (text || '').trim();
  if (!t) return { ok: true, value: {} };
  try {
    const v = JSON.parse(t);
    if (v === null || typeof v !== 'object' || Array.isArray(v)) return { ok: false, error: 'must be a JSON object like {"beam_size": 5}' };
    return { ok: true, value: v };
  } catch (e) { return { ok: false, error: 'invalid JSON' }; }
}

// Small inline help text under a field.
function Hint({ children }) {
  return <div style={{ fontSize: 12, color: 'var(--text-3)', marginTop: 4, lineHeight: 1.45 }}>{children}</div>;
}

function StatusPill({ status }) {
  const map = {
    pending: { kind: 'idle', text: 'Pending' },
    running: { kind: 'busy', text: 'Running' },
    done: { kind: 'ok', text: 'Done' },
    error: { kind: 'err', text: 'Error' },
  };
  const s = map[status] || map.pending;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
      <StatusDot kind={s.kind} pulse={status === 'running'} />{s.text}
    </span>
  );
}

// ── "how it works" — always visible, three steps ────────────────────────────
function HowItWorks() {
  const steps = [
    ['1', 'Transcribe the source once', 'subgen transcribes your file in its own language. That transcript is the reference everything is measured against.'],
    ['2', 'Run each config', 'Every variant you define runs as its own pass on the live model, producing one subtitle each.'],
    ['3', 'The judge ranks them', "subarr's tournament judge scores each output (hallucinations, repetition, readability, and adequacy vs the reference) and crowns a winner."],
  ];
  return (
    <SectionCard label="What this is">
      <p style={{ margin: '0 0 14px', color: 'var(--text-2)', lineHeight: 1.55 }}>
        Stop guessing which Whisper settings give the best subtitles for tricky content. Pick one file,
        list a few configs to compare, and let the judge tell you which one actually wins — objectively,
        on your own hardware and model.
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
        {steps.map(([n, title, body]) => (
          <div key={n} style={{ background: 'var(--bg-1)', border: '1px solid var(--border-0)', borderRadius: 10, padding: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <span style={{
                width: 22, height: 22, borderRadius: 11, background: 'var(--violet-500)', color: '#fff',
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 700,
              }}>{n}</span>
              <span style={{ fontWeight: 600, fontSize: 13 }}>{title}</span>
            </div>
            <div style={{ fontSize: 12.5, color: 'var(--text-3)', lineHeight: 1.5 }}>{body}</div>
          </div>
        ))}
      </div>
    </SectionCard>
  );
}

// ── collapsible kwargs cheat-sheet ──────────────────────────────────────────
function KnobReference() {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ border: '1px solid var(--border-0)', borderRadius: 10, overflow: 'hidden' }}>
      <button onClick={() => setOpen((o) => !o)} style={{
        width: '100%', textAlign: 'left', background: 'var(--bg-1)', border: 'none', cursor: 'pointer',
        padding: '10px 14px', color: 'var(--text-1)', fontSize: 13, fontWeight: 600,
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      }}>
        <span>Common settings, in plain English</span>
        <span style={{ color: 'var(--text-3)' }}>{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
          {KNOBS.map(([name, type, desc]) => (
            <div key={name} style={{ fontSize: 12.5, lineHeight: 1.5 }}>
              <code style={{ color: 'var(--violet-300, #c4b5fd)', fontFamily: 'var(--font-mono)' }}>{name}</code>
              <span style={{ color: 'var(--text-3)', marginLeft: 8 }}>{type}</span>
              <div style={{ color: 'var(--text-2)', marginTop: 2 }}>{desc}</div>
            </div>
          ))}
          <div style={{ fontSize: 12, color: 'var(--text-3)', marginTop: 2 }}>
            A variant is just a JSON object of these. Empty <code>{'{}'}</code> means "use my current subgen settings".
          </div>
        </div>
      )}
    </div>
  );
}

// ── the config-sweep form ──────────────────────────────────────────────────
function SweepForm({ onRun, disabled, gate }) {
  const [mediaPath, setMediaPath] = useState('');
  const [sourceLang, setSourceLang] = useState('');
  const [variants, setVariants] = useState(() => PRESETS.map(({ label, kwargs }) => ({ label, kwargs })));

  const setVariant = (i, patch) => setVariants((vs) => vs.map((v, j) => (j === i ? { ...v, ...patch } : v)));
  const addVariant = () => setVariants((vs) => [...vs, emptyVariant(vs.length + 1)]);
  const removeVariant = (i) => setVariants((vs) => vs.filter((_, j) => j !== i));
  const usePresets = () => setVariants(PRESETS.map(({ label, kwargs }) => ({ label, kwargs })));

  const labels = variants.map((v) => v.label.trim());
  const dupLabel = labels.some((l, i) => l && labels.indexOf(l) !== i);
  const parsed = variants.map((v) => parseKwargs(v.kwargs));
  const badKwargs = parsed.some((p) => !p.ok);
  const ready = mediaPath.trim() && variants.length > 0 && labels.every(Boolean) && !dupLabel && !badKwargs;

  const submit = () => {
    if (!ready) return;
    onRun({
      media_path: mediaPath.trim().replace(/^\/+/, ''),
      source_language: sourceLang.trim() || null,
      variants: variants.map((v, i) => ({ label: v.label.trim(), kwargs: parsed[i].value })),
    });
  };

  return (
    <SectionCard label="Configure sweep">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        <div style={fieldStyle}>
          <span style={lblStyle}>Media file</span>
          <input value={mediaPath} onChange={(e) => setMediaPath(e.target.value)}
                 placeholder="TV/De Fuckulteit/Season 01/S01E01.mkv" style={inputStyle} />
          <Hint>
            Path relative to your library root (the same form you see on the Coverage page).
            <b> Tip:</b> point it at a short, representative clip with real dialogue. You don't need a whole episode,
            a few minutes is enough to compare configs and it finishes far faster.
          </Hint>
        </div>

        <div style={fieldStyle}>
          <span style={lblStyle}>Source language <span style={{ color: 'var(--text-3)', fontWeight: 400 }}>· optional</span></span>
          <input value={sourceLang} onChange={(e) => setSourceLang(e.target.value)}
                 placeholder="auto-detect" style={{ ...inputStyle, maxWidth: 220 }} />
          <Hint>Leave blank to let Whisper detect it. Set an ISO code (e.g. <code>ko</code>, <code>ja</code>, <code>fr</code>) if you already know it — it nudges accuracy and skips a detection step.</Hint>
        </div>

        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
            <span style={lblStyle}>Config variants</span>
            <button onClick={usePresets} style={linkBtnStyle}>reset to presets</button>
          </div>
          <Hint>Each row is one config to trial. The <code>kwargs</code> box is a JSON object of Whisper settings. Start with the presets below and tweak from there.</Hint>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 10 }}>
            {variants.map((v, i) => {
              const preset = PRESETS.find((p) => p.label === v.label);
              return (
                <div key={i}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                    <input value={v.label} onChange={(e) => setVariant(i, { label: e.target.value })}
                           placeholder="label" style={{ ...inputStyle, width: 160, flex: 'none' }} />
                    <div style={{ flex: 1 }}>
                      <textarea value={v.kwargs} onChange={(e) => setVariant(i, { kwargs: e.target.value })}
                                placeholder='{"beam_size": 5}' rows={1}
                                style={{ ...inputStyle, width: '100%', fontFamily: 'var(--font-mono)', resize: 'vertical', minHeight: 34,
                                         borderColor: parsed[i].ok ? undefined : 'var(--danger)' }} />
                      {!parsed[i].ok && <div style={{ color: 'var(--danger)', fontSize: 12, marginTop: 2 }}>kwargs {parsed[i].error}</div>}
                    </div>
                    <button onClick={() => removeVariant(i)} disabled={variants.length <= 1} title="remove variant" style={iconBtnStyle}>
                      <Glyph char={ICONS.close || '×'} size={14} />
                    </button>
                  </div>
                  {preset && <div style={{ fontSize: 12, color: 'var(--text-3)', margin: '3px 0 0 168px' }}>{preset.why}</div>}
                </div>
              );
            })}
          </div>
          <button onClick={addVariant} style={{ ...ghostBtnStyle, marginTop: 10 }}>+ Add variant</button>
          {dupLabel && <div style={{ color: 'var(--danger)', fontSize: 12, marginTop: 6 }}>Variant labels must be unique.</div>}
          <div style={{ marginTop: 12 }}><KnobReference /></div>
        </div>

        {gate && !gate.ok && (
          <div style={gateNoticeStyle}>
            {gate.reason}
            {gate.remedy && <div style={{ marginTop: 4, color: 'var(--text-3)' }}>{gate.remedy}</div>}
          </div>
        )}

        <div style={{ borderTop: '1px solid var(--border-0)', paddingTop: 14 }}>
          <button onClick={submit} disabled={!ready || disabled} style={primaryBtnStyle}>
            {disabled ? 'Sweep running…' : `Run sweep · ${variants.length} config${variants.length === 1 ? '' : 's'}`}
          </button>
          <Hint>
            Heads up: this runs <b>{variants.length} + 1</b> Whisper passes on your GPU (one per config, plus the source transcript),
            so it takes roughly as long as transcribing that clip {variants.length + 1} times. Progress streams live below, and you can
            leave the page and come back.
          </Hint>
        </div>
      </div>
    </SectionCard>
  );
}

// ── live progress + ranked result ───────────────────────────────────────────
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
              <div style={{ fontSize: 13, color: 'var(--text-2)' }}>
                Make it your default by putting these in your subgen config:
              </div>
              <code style={{ display: 'block', marginTop: 6, fontFamily: 'var(--font-mono)', fontSize: 12.5,
                             background: 'var(--bg-0)', padding: '8px 10px', borderRadius: 6, overflowX: 'auto' }}>
                SUBGEN_KWARGS={JSON.stringify(winnerKwargs || {})}
              </code>
              <div style={{ fontSize: 11.5, color: 'var(--text-3)', marginTop: 6 }}>
                (A one-click "adopt winner" is coming — for now, copy + paste.)
              </div>
            </div>
          )}
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ textAlign: 'left', color: 'var(--text-3)' }}>
                <th style={thStyle}>#</th>
                <th style={thStyle}>Config</th>
                {['Composite', 'QE', 'Readability', 'Cues'].map((c) => (
                  <th key={c} style={thStyle} title={COL_HELP[c]}>{c} <span style={{ color: 'var(--text-3)' }}>ⓘ</span></th>
                ))}
              </tr>
            </thead>
            <tbody>
              {scorecards.map((sc, i) => {
                const isWin = sc.entrant_label === winner;
                return (
                  <tr key={sc.entrant_label} style={{ borderTop: '1px solid var(--border-0)',
                       background: isWin ? 'var(--accent-soft, rgba(34,211,161,0.08))' : undefined }}>
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
        <div style={{ color: 'var(--text-3)' }}>
          No scorable candidates — every config was disqualified or produced no subtitle. Try a clip with clearer dialogue,
          or set the source language explicitly.
        </div>
      ) : null}

      {run.status === 'done' && run.source_text == null && (
        <div style={{ marginTop: 10, fontSize: 12, color: 'var(--text-3)' }}>
          No source transcript was produced, so adequacy (QE) was skipped — ranking used structural quality only.
        </div>
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
      setGate(ok ? { ok: true } : {
        ok: false,
        reason: 'This subgen build does not support the tuning lab (needs subarr-subgen ≥ v4.10).',
        remedy: 'Upgrade to ghcr.io/coaxk/subarr-subgen:latest (≥ 2026.05.3-r4).',
      });
    }).catch(() => {});
    return () => { alive = false; };
  }, []);

  const closeStream = useCallback(() => { if (esRef.current) { esRef.current.close(); esRef.current = null; } }, []);

  const refresh = useCallback((id) => {
    fetch(`/api/arena/${id}`).then((r) => r.json()).then((d) => {
      setRun(d);
      if (d.status === 'done' || d.status === 'error') { setBusy(false); closeStream(); }
    }).catch(() => {});
  }, [closeStream]);

  const onRun = useCallback(async (body) => {
    setBusy(true);
    setRun(null);
    try {
      const r = await fetch('/api/arena/run', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      const data = await r.json();
      if (!r.ok) {
        const detail = data?.detail;
        setRun({ status: 'error', error: typeof detail === 'string' ? detail : (detail?.reason || 'request failed'),
                 variants: body.variants, outcomes: [] });
        setBusy(false);
        return;
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
        <SweepForm onRun={onRun} disabled={busy} gate={gate} />
        <ResultPanel run={run} />
      </div>
    </div>
  );
}

// ── inline styles (match the home-hifi token system) ─────────────────────────
const fieldStyle = { display: 'flex', flexDirection: 'column', gap: 6 };
const lblStyle = { fontSize: 13, fontWeight: 600, color: 'var(--text-1)' };
const inputStyle = { background: 'var(--bg-1)', border: '1px solid var(--border-0)', borderRadius: 8, padding: '8px 10px', color: 'var(--text-0)', fontSize: 14, outline: 'none' };
const thStyle = { padding: '6px 8px', fontWeight: 600, fontSize: 12, cursor: 'help' };
const tdStyle = { padding: '8px 8px' };
const primaryBtnStyle = { background: 'var(--accent)', color: 'var(--bg-0)', border: 'none', borderRadius: 8, padding: '10px 18px', fontWeight: 600, fontSize: 14, cursor: 'pointer' };
const ghostBtnStyle = { background: 'transparent', color: 'var(--text-1)', border: '1px solid var(--border-0)', borderRadius: 8, padding: '6px 12px', fontSize: 13, cursor: 'pointer' };
const linkBtnStyle = { background: 'transparent', color: 'var(--violet-300, #c4b5fd)', border: 'none', fontSize: 12, cursor: 'pointer', padding: 0 };
const iconBtnStyle = { background: 'transparent', color: 'var(--text-3)', border: '1px solid var(--border-0)', borderRadius: 8, padding: '8px 10px', cursor: 'pointer', flex: 'none' };
const gateNoticeStyle = { background: 'var(--warn-soft, rgba(245,158,11,0.10))', border: '1px solid var(--warn, #f59e0b)', borderRadius: 8, padding: '10px 12px', fontSize: 13, color: 'var(--text-1)' };
const winnerBoxStyle = { background: 'var(--accent-soft, rgba(34,211,161,0.08))', border: '1px solid var(--accent, #22d3a1)', borderRadius: 10, padding: '12px 14px', marginBottom: 14 };
