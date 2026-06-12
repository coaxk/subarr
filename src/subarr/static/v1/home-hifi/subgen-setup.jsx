// Guided subgen setup — shared flow component (spec §2.6).
//
// Mounted in two places: the onboarding wizard (step `subgen-setup`,
// right after the subgen connection step) and Settings → Subgen tuning.
// All business decisions (model fit, compute derivation, delivery-mode
// routing) live in the /api/subgen-setup/* backend — this component
// renders and relays.

import { SectionCard } from './atoms.jsx';

const { useState, useEffect, useCallback } = React;

const MODELS = ['tiny', 'base', 'small', 'medium', 'large-v3'];

const FIT_LABEL = {
  fits: 'fits comfortably',
  tight: 'tight — int8 variant advised',
  no_fit: 'will not fit',
};
const FIT_COLOR = {
  fits: 'var(--ok-500, #34d399)',
  tight: 'var(--warn-500)',
  no_fit: 'var(--error-500)',
};

function Hint({ children }) {
  return <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-3)', marginTop: 4, lineHeight: 1.5 }}>{children}</div>;
}

function Row({ label, value, hint, control }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, padding: '6px 0', borderBottom: '1px solid var(--bg-3)' }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 'var(--text-md)', color: 'var(--fg-0)' }}>{label}</div>
        {hint && <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)', marginTop: 2 }}>{hint}</div>}
      </div>
      {control || <span className="mono" style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-1)', maxWidth: 360, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{value ?? '—'}</span>}
    </div>
  );
}

function Pre({ children }) {
  return (
    <pre style={{
      margin: 0, padding: 12,
      background: 'var(--bg-0)', border: 'var(--border)',
      borderRadius: 'var(--radius-md)',
      fontSize: 'var(--text-xs)', color: 'var(--fg-1)',
      fontFamily: 'var(--font-mono)',
      whiteSpace: 'pre-wrap', wordBreak: 'break-all',
      maxHeight: 320, overflow: 'auto',
    }}>{children}</pre>
  );
}

function gb(mb) {
  return (mb / 1024).toFixed(1);
}

async function postJson(path, body) {
  const r = await fetch(path, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function SubgenSetupFlow({ onComplete }) {
  const [detect, setDetect] = useState(null);
  const [detectErr, setDetectErr] = useState(null);
  // Effective GPU: detect's auto-found card, or one parsed from a paste.
  const [gpu, setGpu] = useState(null);
  const [cpuMode, setCpuMode] = useState(false);
  const [model, setModel] = useState(null);
  const [plan, setPlan] = useState(null);
  const [planErr, setPlanErr] = useState(null);
  const [pasted, setPasted] = useState('');
  const [parseErr, setParseErr] = useState(null);
  const [parsing, setParsing] = useState(false);
  const [applying, setApplying] = useState(false);
  const [applyResult, setApplyResult] = useState(null);

  // 1. On mount: run the detection cascade.
  useEffect(() => {
    let live = true;
    fetch('/api/subgen-setup/detect', { credentials: 'same-origin' })
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((d) => {
        if (!live) return;
        setDetect(d);
        if (d.gpu) {
          setGpu(d.gpu);
          setModel(d.recommended_model || 'small');
        }
      })
      .catch((e) => { if (live) setDetectErr(String(e.message || e)); });
    return () => { live = false; };
  }, []);

  // 3. Re-plan on every model or gpu change.
  useEffect(() => {
    if (!model) return;
    let live = true;
    setPlanErr(null);
    setApplyResult(null);
    postJson('/api/subgen-setup/plan', {
      model,
      vram_total_mb: gpu ? gpu.vram_total_mb : null,
      compute_cap: gpu ? gpu.compute_cap : null,
    })
      .then((p) => { if (live) setPlan(p); })
      .catch((e) => { if (live) { setPlan(null); setPlanErr(String(e.message || e)); } });
    return () => { live = false; };
  }, [model, gpu]);

  const onParse = useCallback(async () => {
    setParsing(true);
    setParseErr(null);
    try {
      const r = await postJson('/api/subgen-setup/parse-smi', { pasted });
      if (!r.ok) {
        setParseErr(r.error || 'could not parse that output');
      } else {
        setGpu(r.gpu);
        setCpuMode(false);
        setModel(r.recommended_model || 'small');
      }
    } catch (e) {
      setParseErr(String(e.message || e));
    } finally {
      setParsing(false);
    }
  }, [pasted]);

  const onCpu = useCallback(() => {
    setGpu(null);
    setCpuMode(true);
    setModel('small');
  }, []);

  const onApply = useCallback(async () => {
    if (!plan) return;
    setApplying(true);
    setApplyResult(null);
    try {
      const r = await postJson('/api/subgen-setup/apply', {
        model: plan.model,
        compute_type: plan.compute_type,
        vram_total_mb: gpu ? gpu.vram_total_mb : null,
      });
      setApplyResult(r);
      if (r.ok && onComplete) onComplete();
    } catch (e) {
      setApplyResult({ ok: false, reason: 'request_failed', detail: String(e.message || e) });
    } finally {
      setApplying(false);
    }
  }, [plan, gpu, onComplete]);

  if (detectErr) {
    return (
      <div style={{ padding: 12, color: 'var(--error-500)', fontSize: 'var(--text-sm)' }}>
        Hardware detection failed: {detectErr}
      </div>
    );
  }
  if (!detect) {
    return <div style={{ padding: 12, color: 'var(--fg-3)', fontSize: 'var(--text-sm)' }}>Detecting hardware…</div>;
  }

  const current = detect.subgen_current;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 820 }}>
      {/* ── Hardware card ───────────────────────────────────────── */}
      <SectionCard label="Your hardware">
        {gpu ? (
          <div>
            <Row label="GPU" value={gpu.name} />
            <Row label="VRAM" value={`${gb(gpu.vram_total_mb)} GB`} />
            <Row label="Compute capability" value={gpu.compute_cap != null ? String(gpu.compute_cap) : '—'} />
            {gpu.vram_free_mb != null && (
              <Hint>
                Live snapshot: {gb(gpu.vram_total_mb - gpu.vram_free_mb)} GB of {gb(gpu.vram_total_mb)} GB in use right now.
                This is a single moment in time, not your norm. Choose your model based on how much
                VRAM is usually free when subgen runs — if this card also handles Plex transcoding,
                Ollama, or anything else, leave headroom for those at their busiest, not for how
                quiet (or busy) it happens to be this second.
              </Hint>
            )}
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-1)', lineHeight: 1.55 }}>
              {detect.host_gpu_capable
                ? "Your Docker host can do GPU passthrough, but subarr can't read the card directly — it doesn't have detection access. Run this and paste the result:"
                : 'No GPU detected. If you do have an Nvidia GPU, run this and paste the result — otherwise continue on CPU.'}
            </div>
            <Pre>{detect.smi_command}</Pre>
            <textarea
              value={pasted}
              onChange={(e) => setPasted(e.target.value)}
              placeholder="paste the command output here"
              rows={3}
              style={{
                width: '100%', boxSizing: 'border-box', padding: 10,
                background: 'var(--bg-0)', border: 'var(--border)',
                borderRadius: 'var(--radius-md)', color: 'var(--fg-1)',
                fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)',
                resize: 'vertical',
              }}
            />
            {parseErr && (
              <div style={{ fontSize: 'var(--text-sm)', color: 'var(--error-500)' }}>{parseErr}</div>
            )}
            <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
              <button className="btn sm" onClick={onParse} disabled={parsing || !pasted.trim()}>
                {parsing ? 'Parsing…' : 'Parse'}
              </button>
              <button className="btn ghost sm" onClick={onCpu}>Continue without GPU (CPU)</button>
            </div>
            <Hint>
              No nvidia-smi on the host? Run it through a GPU container instead:{' '}
              <span className="mono" style={{ color: 'var(--fg-2)' }}>docker exec &lt;gpu-container&gt; {detect.smi_command}</span>
            </Hint>
            {detect.passthrough_snippet && (
              <Hint>
                Want this automatic next time? Add this under subarr's service so it can read the
                card directly (utility-only — it reserves no VRAM or compute from subgen):
                <div style={{ marginTop: 8 }}><Pre>{detect.passthrough_snippet}</Pre></div>
              </Hint>
            )}
          </div>
        )}
      </SectionCard>

      {/* ── Model card ──────────────────────────────────────────── */}
      {(gpu || model) && (
        <SectionCard label="Whisper model">
          <div>
            {MODELS.map((m) => {
              const selected = m === model;
              const fit = selected && plan && plan.model === m ? plan.fit : null;
              return (
                <div key={m}
                  onClick={() => setModel(m)}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 12,
                    padding: '8px 10px', cursor: 'pointer',
                    borderRadius: 'var(--radius-md)',
                    border: `1px solid ${selected ? 'var(--violet-500)' : 'transparent'}`,
                    background: selected ? 'rgba(139,92,246,0.08)' : 'transparent',
                  }}>
                  <span className="mono" style={{
                    flex: 'none', width: 90,
                    fontSize: 'var(--text-sm)',
                    color: selected ? 'var(--fg-0)' : 'var(--fg-1)',
                    fontWeight: selected ? 600 : 400,
                  }}>{m}</span>
                  <span style={{ flex: 1 }} />
                  {gpu && detect.recommended_model === m && (
                    <span className="chip" style={{ fontSize: 'var(--text-2xs)', color: 'var(--violet-400)' }}>recommended</span>
                  )}
                  {fit && (
                    <span style={{ fontSize: 'var(--text-xs)', color: FIT_COLOR[fit] || 'var(--fg-2)' }}>
                      {FIT_LABEL[fit] || fit}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
          {planErr && (
            <div style={{ fontSize: 'var(--text-sm)', color: 'var(--error-500)' }}>Planning failed: {planErr}</div>
          )}
        </SectionCard>
      )}

      {/* ── Recommended config card ─────────────────────────────── */}
      {plan && (
        <SectionCard label="Recommended config">
          <div>
            <Row label="Device" value={plan.device} />
            <Row label="Compute type" value={plan.compute_type} hint={plan.compute_reason} />
          </div>
          {current && current.whisper_model && (
            <Hint>
              Currently: {current.whisper_model} / {current.transcribe_device || '—'} / {current.compute_type || '—'}
              {' '}→ recommended: {plan.model} / {plan.device} / {plan.compute_type}.
              Tuned kwargs + regroup come baked in the subarr-subgen image.
            </Hint>
          )}

          {/* ── Delivery ──────────────────────────────────────── */}
          {plan.mode === 'live_apply' ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div>
                <button className="btn" onClick={onApply} disabled={applying}>
                  {applying ? 'Applying…' : 'Apply now'}
                </button>
              </div>
              {applyResult && applyResult.ok && (
                <div style={{ fontSize: 'var(--text-sm)', color: 'var(--ok-500, #34d399)' }}>
                  Applied — subgen is now running {applyResult.model || plan.model}.
                </div>
              )}
              {applyResult && !applyResult.ok && (
                <div style={{ fontSize: 'var(--text-sm)', color: 'var(--error-500)', lineHeight: 1.5 }}>
                  {applyResult.rolled_back === false
                    ? 'subgen could not restore the previous model after the failed switch — restart the subgen container, then re-run detection.'
                    : applyResult.reason === 'precheck_no_fit' || applyResult.reason === 'oom'
                      ? `${plan.model} didn't fit on your GPU — kept ${applyResult.current_model || 'your current model'}. Try an int8 variant or free up VRAM and retry.`
                      : applyResult.reason === 'subgen_unreachable'
                        ? applyResult.detail
                        : `Apply failed: ${applyResult.reason}${applyResult.detail ? ` — ${applyResult.detail}` : ''}`}
                </div>
              )}
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <Hint>
                {plan.mode === 'generate_full'
                  ? "No subgen detected — here's a ready-to-run compose for subarr-subgen with your hardware-matched settings:"
                  : plan.upsell === 'switch_to_subarr_subgen'
                    ? "Add these to your subgen service's environment, then docker compose up -d. (Switching to the subarr-subgen image also gets you the tuned defaults + hands-off apply.)"
                    : "Add these to your subgen service's environment, then docker compose up -d. (Upgrading to subarr-subgen r9+ unlocks one-click apply from this page.)"}
              </Hint>
              <Pre>{plan.compose || plan.env_additions}</Pre>
            </div>
          )}
        </SectionCard>
      )}
    </div>
  );
}
