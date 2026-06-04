// #131 — Tuning Lab: drive a subgen config sweep against the live model and
// rank the outputs with the validated tournament judge.
//
// Flow: pick a media file + define N config variants (each a per-request
// SUBGEN_KWARGS override) → POST /api/arena/run → stream /events (SSE) for
// live progress → render the ranked scorecard. The whole thing rides subgen's
// v4.10 /asr path-input channel, so there's no upload and no shared scratch.

import { SectionCard, StatusDot, Glyph, ICONS } from './atoms.jsx';

const { useState, useEffect, useRef, useCallback } = React;

// A couple of ready-made starting points so the page isn't a blank form.
const PRESET_VARIANTS = [
  { label: 'default', kwargs: '{}' },
  { label: 'noisy-robust', kwargs: '{"vad_filter": true, "beam_size": 5, "temperature": 0}' },
];

function emptyVariant(n) {
  return { label: `variant-${n}`, kwargs: '{}' };
}

// Parse a variant's kwargs textarea → {ok, value|error}. Empty = {} (valid).
function parseKwargs(text) {
  const t = (text || '').trim();
  if (!t) return { ok: true, value: {} };
  try {
    const v = JSON.parse(t);
    if (v === null || typeof v !== 'object' || Array.isArray(v)) {
      return { ok: false, error: 'must be a JSON object' };
    }
    return { ok: true, value: v };
  } catch (e) {
    return { ok: false, error: 'invalid JSON' };
  }
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
      <StatusDot kind={s.kind} pulse={status === 'running'} />
      {s.text}
    </span>
  );
}

// ── the config-sweep form ──────────────────────────────────────────────────
function SweepForm({ onRun, disabled, gate }) {
  const [mediaPath, setMediaPath] = useState('');
  const [sourceLang, setSourceLang] = useState('');
  const [variants, setVariants] = useState(PRESET_VARIANTS);

  const setVariant = (i, patch) =>
    setVariants((vs) => vs.map((v, j) => (j === i ? { ...v, ...patch } : v)));
  const addVariant = () => setVariants((vs) => [...vs, emptyVariant(vs.length + 1)]);
  const removeVariant = (i) => setVariants((vs) => vs.filter((_, j) => j !== i));

  const labels = variants.map((v) => v.label.trim());
  const dupLabel = labels.some((l, i) => l && labels.indexOf(l) !== i);
  const parsed = variants.map((v) => parseKwargs(v.kwargs));
  const badKwargs = parsed.some((p) => !p.ok);
  const ready = mediaPath.trim() && variants.length > 0 &&
    labels.every(Boolean) && !dupLabel && !badKwargs;

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
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <label style={fieldStyle}>
          <span style={lblStyle}>Media file <span style={{ color: 'var(--text-3)' }}>(library-relative path)</span></span>
          <input
            value={mediaPath}
            onChange={(e) => setMediaPath(e.target.value)}
            placeholder="TV/De Fuckulteit/Season 01/S01E01.mkv"
            style={inputStyle}
          />
        </label>

        <label style={fieldStyle}>
          <span style={lblStyle}>Source language <span style={{ color: 'var(--text-3)' }}>(optional ISO hint, e.g. ko)</span></span>
          <input
            value={sourceLang}
            onChange={(e) => setSourceLang(e.target.value)}
            placeholder="auto-detect"
            style={{ ...inputStyle, maxWidth: 220 }}
          />
        </label>

        <div>
          <div style={{ ...lblStyle, marginBottom: 8 }}>Config variants</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {variants.map((v, i) => (
              <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                <input
                  value={v.label}
                  onChange={(e) => setVariant(i, { label: e.target.value })}
                  placeholder="label"
                  style={{ ...inputStyle, width: 150, flex: 'none' }}
                />
                <div style={{ flex: 1 }}>
                  <textarea
                    value={v.kwargs}
                    onChange={(e) => setVariant(i, { kwargs: e.target.value })}
                    placeholder='{"beam_size": 5}'
                    rows={1}
                    style={{
                      ...inputStyle, width: '100%', fontFamily: 'var(--font-mono)',
                      resize: 'vertical', minHeight: 34,
                      borderColor: parsed[i].ok ? undefined : 'var(--danger)',
                    }}
                  />
                  {!parsed[i].ok && (
                    <div style={{ color: 'var(--danger)', fontSize: 12, marginTop: 2 }}>
                      kwargs {parsed[i].error}
                    </div>
                  )}
                </div>
                <button
                  onClick={() => removeVariant(i)}
                  disabled={variants.length <= 1}
                  title="remove variant"
                  style={iconBtnStyle}
                >
                  <Glyph char={ICONS.close || '×'} size={14} />
                </button>
              </div>
            ))}
          </div>
          <button onClick={addVariant} style={{ ...ghostBtnStyle, marginTop: 8 }}>+ Add variant</button>
          {dupLabel && (
            <div style={{ color: 'var(--danger)', fontSize: 12, marginTop: 6 }}>
              Variant labels must be unique.
            </div>
          )}
        </div>

        {gate && !gate.ok && (
          <div style={gateNoticeStyle}>
            {gate.reason}
            {gate.remedy && <div style={{ marginTop: 4, color: 'var(--text-3)' }}>{gate.remedy}</div>}
          </div>
        )}

        <div>
          <button onClick={submit} disabled={!ready || disabled} style={primaryBtnStyle}>
            {disabled ? 'Sweep running…' : 'Run sweep'}
          </button>
        </div>
      </div>
    </SectionCard>
  );
}

// ── live progress + ranked result ────────────────────────────────────────────
function ResultPanel({ run }) {
  if (!run) return null;
  const scorecards = run.result?.scorecards || [];
  const winner = run.result?.winner_label;

  return (
    <SectionCard
      label="Sweep result"
      action={<StatusPill status={run.status} />}
    >
      {run.error && (
        <div style={{ color: 'var(--danger)', marginBottom: 12 }}>Error: {run.error}</div>
      )}

      {/* live progress before the judge has ranked */}
      {run.status === 'running' && (
        <div style={{ marginBottom: 14, fontSize: 13, color: 'var(--text-2)' }}>
          <div>Source transcript: {run.source_text != null ? 'ready' : 'transcribing…'}</div>
          <div style={{ marginTop: 4 }}>
            Variants: {run.outcomes.length}/{run.variants.length} processed
          </div>
        </div>
      )}

      {scorecards.length > 0 ? (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ textAlign: 'left', color: 'var(--text-3)' }}>
              <th style={thStyle}>#</th>
              <th style={thStyle}>Variant</th>
              <th style={thStyle}>Composite</th>
              <th style={thStyle}>QE</th>
              <th style={thStyle}>Readability</th>
              <th style={thStyle}>Cues</th>
            </tr>
          </thead>
          <tbody>
            {scorecards.map((sc, i) => {
              const isWin = sc.entrant_label === winner;
              return (
                <tr key={sc.entrant_label} style={{
                  borderTop: '1px solid var(--border-0)',
                  background: isWin ? 'var(--accent-soft, rgba(34,211,161,0.08))' : undefined,
                }}>
                  <td style={tdStyle}>{i + 1}</td>
                  <td style={{ ...tdStyle, fontWeight: isWin ? 700 : 500 }}>
                    {isWin && <span title="winner" style={{ marginRight: 6 }}>★</span>}
                    {sc.entrant_label}
                    {sc.disqualified && (
                      <span style={{ color: 'var(--danger)', marginLeft: 6, fontSize: 11 }}>DQ</span>
                    )}
                  </td>
                  <td style={tdStyle}>{sc.composite?.toFixed(1)}</td>
                  <td style={tdStyle}>
                    {sc.qe_adequacy != null ? sc.qe_adequacy.toFixed(3) : '—'}
                  </td>
                  <td style={tdStyle}>{sc.readability_score?.toFixed(0)}</td>
                  <td style={tdStyle}>{sc.cue_count}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : run.status === 'done' ? (
        <div style={{ color: 'var(--text-3)' }}>No scorable candidates — every variant was disqualified or produced no subtitle.</div>
      ) : null}

      {run.status === 'done' && run.source_text == null && (
        <div style={{ marginTop: 10, fontSize: 12, color: 'var(--text-3)' }}>
          No source transcript was produced, so QE/adequacy was skipped (structural scoring only).
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

  // Probe capability once so the form can warn before the user submits.
  useEffect(() => {
    let alive = true;
    fetch('/api/integrations/health')
      .then((r) => r.json())
      .then((d) => {
        if (!alive) return;
        const ok = !!d?.subgen?.asr_arena;
        setGate(ok ? { ok: true } : {
          ok: false,
          reason: 'This subgen build does not support the tuning lab (needs subarr-subgen ≥ v4.10).',
          remedy: 'Upgrade to ghcr.io/coaxk/subarr-subgen:latest (≥ 2026.05.3-r4).',
        });
      })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  const closeStream = useCallback(() => {
    if (esRef.current) { esRef.current.close(); esRef.current = null; }
  }, []);

  // Refresh the full run state from the API (authoritative; the SSE events
  // are just nudges to re-read so we never drift from server truth).
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
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
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
      // Subscribe to live progress; each event just triggers a state re-read.
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
          <p style={{ color: 'var(--text-2)', marginTop: 6, maxWidth: 620 }}>
            Trial Whisper configs against one file on the live model and let the tournament judge rank them.
            Runs over subgen's <code>/asr</code> channel — no upload, no library changes.
          </p>
        </header>
        <SweepForm onRun={onRun} disabled={busy} gate={gate} />
        <ResultPanel run={run} />
      </div>
    </div>
  );
}

// ── inline styles (match the home-hifi token system) ─────────────────────────
const fieldStyle = { display: 'flex', flexDirection: 'column', gap: 6 };
const lblStyle = { fontSize: 13, fontWeight: 600, color: 'var(--text-2)' };
const inputStyle = {
  background: 'var(--bg-1)', border: '1px solid var(--border-0)', borderRadius: 8,
  padding: '8px 10px', color: 'var(--text-0)', fontSize: 14, outline: 'none',
};
const thStyle = { padding: '6px 8px', fontWeight: 600, fontSize: 12 };
const tdStyle = { padding: '8px 8px' };
const primaryBtnStyle = {
  background: 'var(--accent)', color: 'var(--bg-0)', border: 'none', borderRadius: 8,
  padding: '10px 18px', fontWeight: 600, fontSize: 14, cursor: 'pointer',
};
const ghostBtnStyle = {
  background: 'transparent', color: 'var(--text-1)', border: '1px solid var(--border-0)',
  borderRadius: 8, padding: '6px 12px', fontSize: 13, cursor: 'pointer',
};
const iconBtnStyle = {
  background: 'transparent', color: 'var(--text-3)', border: '1px solid var(--border-0)',
  borderRadius: 8, padding: '8px 10px', cursor: 'pointer', flex: 'none',
};
const gateNoticeStyle = {
  background: 'var(--warn-soft, rgba(245,158,11,0.10))', border: '1px solid var(--warn, #f59e0b)',
  borderRadius: 8, padding: '10px 12px', fontSize: 13, color: 'var(--text-1)',
};
