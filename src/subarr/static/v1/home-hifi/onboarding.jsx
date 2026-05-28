// Onboarding wizard — 10-step state machine wired to /api/onboarding/*.
//
// State is persisted server-side in the onboarding_state table; this
// frontend is a thin consumer. Refreshing the page resumes exactly
// where the user left off. The wizard is one-shot per install but
// re-runnable from Settings → Re-run setup.

import { Wordmark, StatusDot } from './atoms.jsx';

const { useState, useEffect, useCallback } = React;

// Step IDs MUST match the backend's STEP_* constants (subarr/onboarding.py).
const STEPS = [
  { id: 'welcome',  label: 'Welcome',       group: 'intro' },
  { id: 'paths',    label: 'Library paths', group: 'intro' },
  { id: 'bazarr',   label: 'Bazarr',        group: 'integrations', service: 'bazarr' },
  { id: 'sonarr',   label: 'Sonarr',        group: 'integrations', service: 'sonarr' },
  { id: 'radarr',   label: 'Radarr',        group: 'integrations', service: 'radarr', optional: true },
  { id: 'tautulli', label: 'Tautulli',      group: 'integrations', service: 'tautulli', optional: true },
  { id: 'subgen',   label: 'subgen',        group: 'integrations', service: 'subgen' },
  { id: 'ollama',   label: 'Ollama',        group: 'integrations', service: 'ollama', optional: true },
  { id: 'gpu',      label: 'GPU check',     group: 'review' },
  { id: 'walk',     label: 'First walk',    group: 'review' },
];

// ─── API client ─────────────────────────────────────────────────────


const Api = {
  async getState() {
    const r = await fetch('/api/onboarding/state', { credentials: 'same-origin' });
    return r.ok ? r.json() : null;
  },
  async putState(patch) {
    const r = await fetch('/api/onboarding/state', {
      method: 'PUT',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    return r.ok ? r.json() : null;
  },
  async test(service, url, apiKey) {
    const r = await fetch(`/api/onboarding/test/${service}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, api_key: apiKey || null }),
    });
    return r.ok ? r.json() : { ok: false, error: `HTTP ${r.status}` };
  },
  async autoDetect() {
    const r = await fetch('/api/onboarding/auto-detect', { method: 'POST' });
    return r.ok ? r.json() : { available: false };
  },
  async probePaths(mediaRoot) {
    const r = await fetch('/api/onboarding/probe-paths', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ media_root: mediaRoot }),
    });
    return r.ok ? r.json() : { ok: false, error: `HTTP ${r.status}` };
  },
  async firstWalk() {
    const r = await fetch('/api/onboarding/first-walk', { method: 'POST' });
    return r.ok ? r.json() : { walks: [] };
  },
  async complete() {
    const r = await fetch('/api/onboarding/complete', { method: 'POST' });
    return r.ok ? r.json() : null;
  },
};


// ─── Stepper ─────────────────────────────────────────────────────


function Stepper({ current }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 0 }}>
      {STEPS.map((s, i) => {
        const done = i < current;
        const active = i === current;
        const dotSize = active ? 24 : 18;
        return (
          <React.Fragment key={s.id}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, minWidth: 22 }}>
              <div style={{
                width: dotSize, height: dotSize,
                borderRadius: '50%',
                border: `1.5px solid ${active || done ? 'var(--violet-500)' : 'var(--bg-4)'}`,
                background: done ? 'var(--violet-500)' : active ? 'var(--bg-0)' : 'transparent',
                color: done ? '#fff' : active ? 'var(--violet-400)' : 'var(--fg-3)',
                fontSize: active ? 11 : 10,
                fontWeight: 600,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontFamily: 'var(--font-mono)',
                transition: 'all var(--dur-base) var(--ease-out)',
              }}>{done ? '✓' : i + 1}</div>
            </div>
            {i < STEPS.length - 1 && (
              <div style={{ flex: 1, height: 1.5, background: i < current ? 'var(--violet-500)' : 'var(--bg-4)', margin: '0 4px' }} />
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
}


// ─── Form primitives ─────────────────────────────────────────────


function FormRow({ label, hint, children }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-1)', fontWeight: 500 }}>{label}</span>
        {hint && <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>{hint}</span>}
      </div>
      {children}
    </label>
  );
}

function TextInput({ value, onChange, placeholder, mono = true, type = 'text' }) {
  const [focused, setFocused] = useState(false);
  return (
    <input
      type={type}
      value={value || ''}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      onFocus={() => setFocused(true)}
      onBlur={() => setFocused(false)}
      className={mono ? 'mono' : ''}
      style={{
        height: 34,
        padding: '0 12px',
        background: 'var(--bg-2)',
        border: `1px solid ${focused ? 'var(--violet-500)' : 'var(--bg-4)'}`,
        boxShadow: focused ? '0 0 0 3px rgba(139,92,246,0.18)' : 'none',
        borderRadius: 'var(--radius-md)',
        color: 'var(--fg-0)',
        fontSize: 'var(--text-md)',
        fontFamily: mono ? 'var(--font-mono)' : 'var(--font-ui)',
        outline: 'none',
        transition: 'border-color var(--dur-fast), box-shadow var(--dur-fast)',
      }}
    />
  );
}

function TestResult({ result }) {
  if (!result) return null;
  const isOk = result.ok;
  const bg = isOk ? 'rgba(52,211,153,0.06)' : 'rgba(239,68,68,0.06)';
  const border = isOk ? 'rgba(52,211,153,0.32)' : 'rgba(239,68,68,0.32)';
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 6,
      padding: '12px 14px',
      background: bg,
      border: `1px solid ${border}`,
      borderRadius: 'var(--radius-md)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <StatusDot kind={isOk ? 'ok' : 'error'} size="lg" />
        <span style={{ fontSize: 'var(--text-md)', fontWeight: 600, color: 'var(--fg-0)' }}>
          {isOk
            ? `${result.version ? `${result.version} · ` : ''}${result.detail || 'connection ok'}`
            : `connection failed`}
        </span>
      </div>
      {!isOk && result.error && (
        <div style={{ paddingLeft: 18 }}>
          <span className="mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--error-500)' }}>
            {result.error}
          </span>
        </div>
      )}
    </div>
  );
}


// ─── Step components ────────────────────────────────────────────


function StepWelcome({ onAutoDetect, detectedCount }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <h1 className="display" style={{ margin: 0, fontSize: 28, fontWeight: 600, letterSpacing: '-0.01em' }}>
        Welcome to subarr
      </h1>
      <p style={{ margin: 0, fontSize: 'var(--text-md)', color: 'var(--fg-1)', lineHeight: 1.55, maxWidth: 540 }}>
        Subarr coordinates Bazarr, Sonarr, Radarr, Tautulli, and subgen to keep your
        library's subtitles in order without burning GPU on stuff you'll never watch.
      </p>
      <p style={{ margin: 0, fontSize: 'var(--text-md)', color: 'var(--fg-1)', lineHeight: 1.55, maxWidth: 540 }}>
        This setup takes about 4 minutes. You can skip optional integrations and
        configure them later from Settings.
      </p>
      <div style={{
        padding: '14px 16px',
        background: 'var(--bg-2)',
        border: 'var(--border)',
        borderRadius: 'var(--radius-md)',
        display: 'flex', flexDirection: 'column', gap: 8,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className="label">auto-detect</span>
          <span style={{ flex: 1 }} />
          <button className="btn" onClick={onAutoDetect}>Detect my stack</button>
        </div>
        <span style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)', lineHeight: 1.5 }}>
          If your *arr containers run alongside subarr with a docker socket proxy mounted,
          I can fill in URLs (and optionally API keys) for you. {detectedCount > 0 && (
            <span style={{ color: 'var(--violet-400)', fontWeight: 600 }}> Detected {detectedCount} service(s) on this host.</span>
          )}
        </span>
      </div>
    </div>
  );
}


function StepPaths({ progress, setField, probeResult, onProbe }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div>
        <h1 className="display" style={{ margin: 0, fontSize: 26, fontWeight: 600, letterSpacing: '-0.01em' }}>
          Where's your library?
        </h1>
        <p style={{ margin: '8px 0 0', fontSize: 'var(--text-md)', color: 'var(--fg-1)', lineHeight: 1.55, maxWidth: 540 }}>
          Path subarr sees inside this container. Bind-mount your media root here
          (e.g. <span className="mono">/mnt/nas/Media:/media/library:ro</span>).
        </p>
      </div>
      <FormRow label="Library root inside container">
        <TextInput
          value={progress.media_root || '/media/library'}
          onChange={(v) => setField('media_root', v)}
          placeholder="/media/library"
        />
      </FormRow>
      <FormRow label="ARR path prefix" hint="how Sonarr/Radarr see their /data — strip when canonicalising">
        <TextInput
          value={progress.arr_path_prefix || '/data/Media/'}
          onChange={(v) => setField('arr_path_prefix', v)}
          placeholder="/data/Media/"
        />
      </FormRow>
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <button className="btn" onClick={onProbe}>Test path</button>
      </div>
      {probeResult && (
        probeResult.ok ? (
          <div style={{ padding: '12px 14px', background: 'rgba(52,211,153,0.06)', border: '1px solid rgba(52,211,153,0.32)', borderRadius: 'var(--radius-md)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
              <StatusDot kind="ok" size="lg" />
              <span style={{ fontWeight: 600 }}>
                Path reachable · {probeResult.total_top_level} top-level items
              </span>
            </div>
            <div className="mono" style={{ paddingLeft: 18, fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
              {probeResult.samples.join('  ·  ')}
            </div>
          </div>
        ) : (
          <div style={{ padding: '12px 14px', background: 'rgba(239,68,68,0.06)', border: '1px solid rgba(239,68,68,0.32)', borderRadius: 'var(--radius-md)' }}>
            <StatusDot kind="error" size="lg" />
            <span style={{ marginLeft: 10 }} className="mono">{probeResult.error}</span>
          </div>
        )
      )}
    </div>
  );
}


function StepIntegration({ step, progress, setField, testResult, onTest, isTesting }) {
  const svc = step.service;
  const urlKey = `${svc}_url`;
  const apiKeyKey = `${svc}_api_key`;
  const labels = {
    bazarr:   { display: 'Bazarr',   port: '6767', help: "Subarr reads Bazarr's wanted list and writes back when subs are generated." },
    sonarr:   { display: 'Sonarr',   port: '8989', help: "Subarr resolves episode → file paths via Sonarr to skip stale-disk gaps." },
    radarr:   { display: 'Radarr',   port: '7878', help: "Same as Sonarr but for movies." },
    tautulli: { display: 'Tautulli', port: '8181', help: "Watch history boosts the gap-list scoring — recently-watched shows get priority." },
    subgen:   { display: 'subgen',   port: '9000', help: "The Whisper worker. Use ghcr.io/coaxk/subarr-subgen for full features, or vanilla mccloud/subgen for compat mode. Model size + precision (tiny → large-v3, float16/int8) are set on subgen's own env vars (WHISPER_MODEL, WHISPER_COMPUTE_TYPE) — subarr just dispatches." },
    ollama:   { display: 'Ollama',   port: '11434', help: "Optional. Used for originalLanguage inference on shows where Sonarr returned null/und." },
  }[svc];
  const placeholderUrl = `http://${svc}:${labels.port}`;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
          <span className="label" style={{ color: 'var(--violet-400)' }}>integrations</span>
          {step.optional && (
            <>
              <span style={{ width: 12, height: 1, background: 'var(--bg-4)' }} />
              <span className="label" style={{ color: 'var(--fg-3)' }}>optional</span>
            </>
          )}
        </div>
        <h1 className="display" style={{ margin: 0, fontSize: 26, fontWeight: 600, letterSpacing: '-0.01em' }}>
          Connect {labels.display}
        </h1>
        <p style={{ margin: '8px 0 0', fontSize: 'var(--text-md)', color: 'var(--fg-1)', lineHeight: 1.55, maxWidth: 540 }}>
          {labels.help}
        </p>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <FormRow label="URL" hint="reachable from this container">
          <TextInput
            value={progress[urlKey] || ''}
            onChange={(v) => setField(urlKey, v)}
            placeholder={placeholderUrl}
          />
        </FormRow>
        {svc !== 'subgen' && svc !== 'ollama' && (
          <FormRow label="API key" hint={`from ${labels.display} → Settings`}>
            <TextInput
              value={progress[apiKeyKey] || ''}
              onChange={(v) => setField(apiKeyKey, v)}
              placeholder="paste API key"
              type="password"
            />
          </FormRow>
        )}
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <button className="btn" disabled={isTesting} onClick={onTest}>
          {isTesting ? 'Testing…' : 'Test connection'}
        </button>
      </div>

      <TestResult result={testResult} />
    </div>
  );
}


function StepGpu({ gpuInfo }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <h1 className="display" style={{ margin: 0, fontSize: 26, fontWeight: 600, letterSpacing: '-0.01em' }}>
        GPU check
      </h1>
      {gpuInfo ? (
        <div style={{ padding: '14px 16px', background: 'rgba(52,211,153,0.06)', border: '1px solid rgba(52,211,153,0.32)', borderRadius: 'var(--radius-md)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
            <StatusDot kind="ok" size="lg" />
            <span style={{ fontWeight: 600 }}>{gpuInfo.name}</span>
          </div>
          <div className="mono" style={{ paddingLeft: 18, fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
            {gpuInfo.vram_total_mb} MiB VRAM · {gpuInfo.temp_c}°C · {gpuInfo.power_w}W / {gpuInfo.power_cap_w}W
          </div>
        </div>
      ) : (
        <div style={{ padding: '14px 16px', background: 'var(--bg-2)', border: 'var(--border)', borderRadius: 'var(--radius-md)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <StatusDot kind="warn" size="lg" />
            <span style={{ fontWeight: 600 }}>No GPU detected via nvidia-smi</span>
          </div>
          <p style={{ margin: '8px 0 0 18px', fontSize: 'var(--text-sm)', color: 'var(--fg-2)', lineHeight: 1.5 }}>
            Subarr works fine without a GPU — but subgen transcription will use CPU,
            which is dramatically slower. If you have an NVIDIA GPU, ensure the
            container has it passed through (nvidia-container-toolkit).
          </p>
        </div>
      )}
    </div>
  );
}


function StepWalk({ progress, walkResult, onStart, isStarting }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div>
        <h1 className="display" style={{ margin: 0, fontSize: 26, fontWeight: 600, letterSpacing: '-0.01em' }}>
          First walk
        </h1>
        <p style={{ margin: '8px 0 0', fontSize: 'var(--text-md)', color: 'var(--fg-1)', lineHeight: 1.55, maxWidth: 540 }}>
          Kicks off the initial probe walk against your library root(s). Discovers
          existing media files + their embedded subtitle tracks, so the dashboard
          shows real coverage on first paint instead of an empty state.
        </p>
      </div>
      <FormRow label="Probe roots" hint="comma-separated, relative to library root">
        <TextInput
          value={(progress.probe_roots || ['TV', 'Movies']).join(', ')}
          onChange={(v) => {/* parsed on submit */}}
          placeholder="TV, Movies"
        />
      </FormRow>
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <button className="btn primary" disabled={isStarting} onClick={onStart}>
          {isStarting ? 'Starting…' : 'Start first walk'}
        </button>
      </div>
      {walkResult && walkResult.walks && walkResult.walks.length > 0 && (
        <div style={{ padding: '14px 16px', background: 'rgba(52,211,153,0.06)', border: '1px solid rgba(52,211,153,0.32)', borderRadius: 'var(--radius-md)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
            <StatusDot kind="ok" size="lg" />
            <span style={{ fontWeight: 600 }}>{walkResult.walks.length} walk(s) started · running in background</span>
          </div>
          <div style={{ paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 2 }}>
            {walkResult.walks.map((w, i) => (
              <span key={i} className="mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
                {w.walk_id ? `${w.walk_id.slice(0, 8)}… → ${w.root}` : `${w.root}: ${w.error}`}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}


// ─── Footer ──────────────────────────────────────────────────────


function WizardFooter({ canBack, canContinue, onBack, onSkip, onContinue, continueLabel, optional }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, paddingTop: 18, borderTop: 'var(--border)' }}>
      <button className="btn ghost" disabled={!canBack} onClick={onBack}>← Back</button>
      <span style={{ flex: 1 }} />
      {optional && (
        <button className="btn ghost" onClick={onSkip}>Skip</button>
      )}
      <button className="btn primary" disabled={!canContinue} onClick={onContinue}>
        {continueLabel || 'Continue →'}
      </button>
    </div>
  );
}


// ─── Page ────────────────────────────────────────────────────────


export function OnboardingPage() {
  const [state, setState] = useState({ step: 0, progress: {} });
  const [loaded, setLoaded] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [probeResult, setProbeResult] = useState(null);
  const [walkResult, setWalkResult] = useState(null);
  const [gpuInfo, setGpuInfo] = useState(null);
  const [autoDetected, setAutoDetected] = useState(0);
  const [busy, setBusy] = useState(false);

  // Initial load — fetch state from backend; if already complete,
  // redirect to /home so the wizard isn't re-shown.
  useEffect(() => {
    Api.getState().then((st) => {
      if (st) {
        if (st.is_complete) {
          window.location.href = '/home';
          return;
        }
        setState({ step: st.step || 0, progress: st.progress || {} });
      }
      setLoaded(true);
    });
    // Also pre-fetch GPU info for the GPU step.
    fetch('/api/gpu').then(r => r.json()).then(g => {
      if (g.online) setGpuInfo({
        name: g.name,
        vram_total_mb: g.memory?.total_mib,
        temp_c: g.temperature_c,
        power_w: g.power?.draw_w,
        power_cap_w: g.power?.limit_w,
      });
    }).catch(() => {});
  }, []);

  const setField = useCallback((key, value) => {
    setState(prev => ({ ...prev, progress: { ...prev.progress, [key]: value } }));
  }, []);

  const persist = async (patch) => {
    const next = await Api.putState(patch);
    if (next) setState({ step: next.step, progress: next.progress || {} });
  };

  const goTo = async (step) => {
    setTestResult(null);
    setProbeResult(null);
    await persist({ step, progress: state.progress });
  };

  const onAutoDetect = async () => {
    setBusy(true);
    const r = await Api.autoDetect();
    setBusy(false);
    if (!r.available) {
      setTestResult({ ok: false, error: r.reason || 'auto-detect unavailable' });
      return;
    }
    const services = r.services || {};
    const patch = {};
    for (const [svc, info] of Object.entries(services)) {
      if (info.candidate?.inferred_url) patch[`${svc}_url`] = info.candidate.inferred_url;
      // We never autofill API keys here — wizard surfaces them per-service
      // with a clear "extracted from <source>" badge.
    }
    setAutoDetected(Object.keys(services).length);
    setState(prev => ({ ...prev, progress: { ...prev.progress, ...patch } }));
  };

  const onTest = async () => {
    const step = STEPS[state.step];
    if (!step.service) return;
    setBusy(true);
    setTestResult({ ok: false, error: 'testing…' });
    const url = state.progress[`${step.service}_url`];
    const key = state.progress[`${step.service}_api_key`];
    const r = await Api.test(step.service, url, key);
    setTestResult(r);
    setBusy(false);
  };

  const onProbe = async () => {
    setBusy(true);
    const r = await Api.probePaths(state.progress.media_root || '/media/library');
    setProbeResult(r);
    setBusy(false);
  };

  const onStartWalk = async () => {
    setBusy(true);
    const r = await Api.firstWalk();
    setWalkResult(r);
    setBusy(false);
  };

  const onFinish = async () => {
    setBusy(true);
    await Api.complete();
    window.location.href = '/home';
  };

  if (!loaded) {
    return (
      <div style={{ minHeight: '100vh', background: 'var(--bg-0)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--fg-3)' }}>
        loading…
      </div>
    );
  }

  const step = STEPS[state.step];
  const isLast = state.step === STEPS.length - 1;
  const continueLabel = isLast ? 'Finish setup →' : 'Continue →';

  // Continue gating per step:
  // - paths needs media_root non-empty
  // - integration steps don't require test-pass (we let users continue
  //   knowing they're skipping the test — better than blocking)
  // - all others always continue-able
  let canContinue = true;
  if (step.id === 'paths' && !state.progress.media_root) canContinue = false;

  const renderStep = () => {
    if (step.id === 'welcome') return <StepWelcome onAutoDetect={onAutoDetect} detectedCount={autoDetected} />;
    if (step.id === 'paths')   return <StepPaths progress={state.progress} setField={setField} probeResult={probeResult} onProbe={onProbe} />;
    if (step.service)          return <StepIntegration step={step} progress={state.progress} setField={setField} testResult={testResult} onTest={onTest} isTesting={busy} />;
    if (step.id === 'gpu')     return <StepGpu gpuInfo={gpuInfo} />;
    if (step.id === 'walk')    return <StepWalk progress={state.progress} walkResult={walkResult} onStart={onStartWalk} isStarting={busy} />;
    return null;
  };

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', background: 'var(--bg-0)' }}>
      <header style={{ height: 56, display: 'flex', alignItems: 'center', padding: '0 var(--space-6)' }}>
        <Wordmark size={18} />
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>
          First-run setup · est. 4 minutes
        </span>
      </header>

      <div style={{ flex: 1, display: 'flex', justifyContent: 'center', alignItems: 'flex-start', padding: '20px 24px 60px' }}>
        <div style={{ width: '100%', maxWidth: 620, display: 'flex', flexDirection: 'column', gap: 26 }}>
          <div>
            <Stepper current={state.step} />
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 10 }}>
              <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)', letterSpacing: '0.04em' }}>
                {state.step} done · 1 in progress · {STEPS.length - 1 - state.step} left
              </span>
              <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--violet-400)', letterSpacing: '0.04em' }}>
                {step.id}
              </span>
            </div>
          </div>

          <div style={{ background: 'var(--bg-1)', border: 'var(--border)', borderRadius: 'var(--radius-lg)', padding: '26px 28px 22px', display: 'flex', flexDirection: 'column', gap: 22 }}>
            {renderStep()}
            <WizardFooter
              canBack={state.step > 0}
              canContinue={canContinue && !busy}
              optional={step.optional}
              continueLabel={continueLabel}
              onBack={() => goTo(state.step - 1)}
              onSkip={() => isLast ? onFinish() : goTo(state.step + 1)}
              onContinue={() => isLast ? onFinish() : goTo(state.step + 1)}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

