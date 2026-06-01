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
  async detectMounts() {
    const r = await fetch('/api/onboarding/detect-mounts', { credentials: 'same-origin' });
    return r.ok ? r.json() : { available: false, candidates: [] };
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


function StepWelcome({ onAutoDetect, detectedCount, autoDetectError }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <h1 className="display" style={{ margin: 0, fontSize: 'var(--text-display-xl)', fontWeight: 600, letterSpacing: '-0.01em' }}>
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
        {/* #129: previously the button click was completely silent when
            discovery wasn't configured (no socket proxy mounted). Users
            assumed it was broken and abandoned. Now we surface the actual
            reason inline AND tell them how to enable it. */}
        {autoDetectError && (
          <div style={{
            marginTop: 4, padding: '10px 12px',
            background: 'rgba(239,158,76,0.08)',
            border: '1px solid rgba(239,158,76,0.32)',
            borderRadius: 'var(--radius-sm)',
            fontSize: 'var(--text-xs)', color: 'var(--fg-1)', lineHeight: 1.5,
          }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>Auto-detect unavailable</div>
            <div style={{ color: 'var(--fg-2)' }}>{autoDetectError}</div>
            <div style={{ marginTop: 6, color: 'var(--fg-3)' }}>
              To enable: mount the docker socket (or a tecnativa/docker-socket-proxy)
              into subarr at <code className="mono">/var/run/docker.sock</code> and
              restart. See <code className="mono">deploy/templates/tier2-socket-proxy.compose.yaml</code>.
              Otherwise, fill the URLs by hand on each step.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}


function StepPaths({ progress, setField, probeResult, onProbe }) {
  // #130: auto-detect bind-mounted media candidates from /proc/self/mountinfo.
  // One round-trip on mount; results render as one-click chips below the
  // library-root input. Non-Linux / non-bound hosts get available:false and
  // the section quietly hides.
  const [mountDetect, setMountDetect] = useState(null);
  React.useEffect(() => {
    Api.detectMounts().then(setMountDetect).catch(() => setMountDetect({ available: false }));
  }, []);

  // #136 fix: seed the displayed defaults into the underlying state once
  // so the Continue button isn't gated on a field that LOOKS filled in but
  // is actually empty. User sees /media/library, expects Continue to work.
  React.useEffect(() => {
    if (!progress.media_root) setField('media_root', '/media/library');
    if (!progress.arr_path_prefix) setField('arr_path_prefix', '/data/Media/');
    // #133: per-service path prefixes. Default to the legacy
    // arr_path_prefix if the user already had it set; otherwise the
    // most common *arr layouts (Sonarr at /data/TV/, Radarr at
    // /data/Movies/).
    if (!progress.sonarr_path_prefix) {
      setField('sonarr_path_prefix', progress.arr_path_prefix || '/data/TV/');
    }
    if (!progress.radarr_path_prefix) {
      setField('radarr_path_prefix', progress.arr_path_prefix || '/data/Movies/');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div>
        <h1 className="display" style={{ margin: 0, fontSize: 'var(--text-display-lg)', fontWeight: 600, letterSpacing: '-0.01em' }}>
          Where's your library?
        </h1>
        <p style={{ margin: '8px 0 0', fontSize: 'var(--text-md)', color: 'var(--fg-1)', lineHeight: 1.55, maxWidth: 540 }}>
          The path subarr sees <b>inside its container</b> — that&apos;s usually
          {' '}<span className="mono">/media/library</span>. Set the host path
          {' '}separately in your compose file, e.g.
          {' '}<span className="mono">/mnt/nas/Media:/media/library:ro</span>.
          <br/>
          <span style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-3)' }}>
            (Container path = where subarr sees files · Host path = where they
            physically are on your machine.)
          </span>
        </p>
      </div>
      {/* #130: detected mount candidates surface as one-click chips so
          the user doesn't have to type the path. Only renders when at
          least one plausible candidate was found. */}
      {mountDetect?.available && mountDetect.candidates?.length > 0 && (
        <div style={{
          padding: '10px 12px',
          background: 'rgba(34,211,161,0.05)',
          border: '1px solid rgba(34,211,161,0.20)',
          borderRadius: 'var(--radius-md)',
          fontSize: 'var(--text-xs)',
          color: 'var(--fg-2)',
          lineHeight: 1.45,
        }}>
          <div style={{ marginBottom: 6 }}>
            <b style={{ color: 'var(--fg-1)' }}>Detected bind-mounts</b> from your container —
            click to use as the library root:
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {mountDetect.candidates.slice(0, 6).map((c) => (
              <button key={c.path}
                onClick={() => setField('media_root', c.path)}
                title={`${c.fs_type || 'mount'}${c.source ? ' from ' + c.source : ''}${c.item_count != null ? ' · ' + c.item_count + ' top-level items' : ''}${c.top_entries?.length ? '\nFirst entries: ' + c.top_entries.join(', ') : ''}`}
                style={{
                  fontSize: 'var(--text-xs)', padding: '4px 10px',
                  background: progress.media_root === c.path ? 'var(--violet-500)' : 'var(--bg-2)',
                  color: progress.media_root === c.path ? '#fff' : 'var(--fg-1)',
                  border: '1px solid var(--bg-4)',
                  borderRadius: 'var(--radius-pill)',
                  cursor: 'pointer',
                  fontFamily: 'var(--font-mono)',
                }}>
                {c.path}
                {c.item_count != null && c.item_count > 0 && (
                  <span style={{ marginLeft: 6, opacity: 0.6 }}>· {c.item_count}</span>
                )}
              </button>
            ))}
          </div>
        </div>
      )}
      {/* #134: per user, label as "container's view" + give explicit guidance
          on where to find each value. The right-hand side of every bind-mount
          line in the relevant docker-compose.yaml IS the answer. */}
      <FormRow label="Subarr's container view of media"
        hint="Look in your subarr compose.yaml under `volumes:` — the part AFTER the colon (e.g. /mnt/nas/Media:/media/library → use /media/library).">
        <TextInput
          value={progress.media_root || '/media/library'}
          onChange={(v) => setField('media_root', v)}
          placeholder="/media/library"
        />
      </FormRow>
      <FormRow label="Sonarr's container view of media"
        hint="Look in your Sonarr compose.yaml under `volumes:` — the right side of the mount that holds your TV library (e.g. /mnt/nas/Media/TV:/data/TV → use /data/TV).">
        <TextInput
          value={progress.sonarr_path_prefix || progress.arr_path_prefix || '/data/TV/'}
          onChange={(v) => setField('sonarr_path_prefix', v)}
          placeholder="/data/TV/"
        />
      </FormRow>
      <FormRow label="Radarr's container view of media"
        hint="Same idea, in your Radarr compose.yaml under `volumes:` — right side of the mount holding your movie library.">
        <TextInput
          value={progress.radarr_path_prefix || progress.arr_path_prefix || '/data/Movies/'}
          onChange={(v) => setField('radarr_path_prefix', v)}
          placeholder="/data/Movies/"
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
  // #141: anticipatory prefill. Once the user has entered ONE working URL
  // (most commonly Sonarr first), every later integration step on the same
  // host probably wants the same scheme+host with that service's default
  // port. Watch all *_url keys in progress and on first mount synthesise
  // this service's URL from whichever earlier service already worked.
  const PORTS = { bazarr: '6767', sonarr: '8989', radarr: '7878', tautulli: '8181', subgen: '9000', ollama: '11434' };
  const detectedContainer = progress.detected_services?.[svc]?.container_name;
  React.useEffect(() => {
    if (progress[urlKey]) return; // user already typed (or we already prefilled)
    const myPort = PORTS[svc];
    if (!myPort) return;
    // #137: prefer the docker-detected container name when we have one —
    // it's the truthiest hostname for an *arr stack on a shared network.
    if (detectedContainer) {
      setField(urlKey, `http://${detectedContainer}:${myPort}`);
      return;
    }
    for (const key of ['sonarr_url', 'radarr_url', 'bazarr_url', 'tautulli_url']) {
      const v = progress[key];
      if (!v || typeof v !== 'string') continue;
      // Match scheme://host:port (port is optional in the source URL).
      const m = v.match(/^(https?:\/\/[^/:]+)(?::\d+)?(\/.*)?$/);
      if (m) { setField(urlKey, `${m[1]}:${myPort}`); return; }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [svc]);
  // #140: dropped "wanted" — Bazarr-jargon for users who haven't lived
  // inside Bazarr; "missing-subs list" reads cleaner cold.
  // #144: Ollama help disambiguates the typical 11434 port vs. the
  // common reverse-proxy / OpenWebUI port confusion + names recommended
  // models so first-time users don't have to spelunk into model catalogs.
  const labels = {
    bazarr:   { display: 'Bazarr',   port: '6767', help: "Subarr reads Bazarr's missing-subs list and writes generated subs back so the list shrinks." },
    sonarr:   { display: 'Sonarr',   port: '8989', help: "Subarr resolves episode → file paths via Sonarr to skip stale-disk gaps." },
    radarr:   { display: 'Radarr',   port: '7878', help: "Same as Sonarr but for movies." },
    tautulli: { display: 'Tautulli', port: '8181', help: "Watch history boosts the gap-list scoring — recently-watched shows get priority." },
    subgen:   { display: 'subgen',   port: '9000', help: "The Whisper worker. Use ghcr.io/coaxk/subarr-subgen for full features, or vanilla mccloud/subgen for compat mode. Model size + precision (tiny → large-v3, float16/int8) are set on subgen's own env vars (WHISPER_MODEL, WHISPER_COMPUTE_TYPE) — subarr just dispatches." },
    ollama:   { display: 'Ollama',   port: '11434', help: "Optional. Used for originalLanguage inference on shows where Sonarr returned null/und. Point at Ollama directly (port 11434), NOT OpenWebUI (3000) — subarr calls /api/generate. Recommended models: qwen2.5:7b (default, fast, low VRAM) or llama3.1:8b. Pull with `ollama pull <model>` before this step." },
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
        <h1 className="display" style={{ margin: 0, fontSize: 'var(--text-display-lg)', fontWeight: 600, letterSpacing: '-0.01em' }}>
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
          {/* #139: spell out the three URL forms so users on different
              topologies don't guess wrong on the first try. Rendered
              below the input as a small dim block — verbose enough to
              be useful, faded enough to skip if you know the answer. */}
          <div style={{
            marginTop: 6, fontSize: 'var(--text-xs)', color: 'var(--fg-3)',
            lineHeight: 1.5,
          }}>
            Try whichever one your subarr container can reach:
            <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>
              <li><code className="mono">{placeholderUrl}</code> — same docker network as {labels.display}</li>
              <li><code className="mono">http://192.168.x.x:{labels.port}</code> — host LAN IP, when containers are on different networks</li>
              <li><code className="mono">http://host.docker.internal:{labels.port}</code> — Docker Desktop on macOS/Windows</li>
              {detectedContainer && (
                <li>
                  Detected container name: <code className="mono">{detectedContainer}</code>
                  {' — '}
                  <button
                    type="button"
                    onClick={() => setField(urlKey, `http://${detectedContainer}:${labels.port}`)}
                    style={{
                      background: 'rgba(167,139,250,0.12)',
                      border: '1px solid rgba(167,139,250,0.4)',
                      borderRadius: 'var(--radius-sm)',
                      color: 'var(--violet-400)',
                      cursor: 'pointer',
                      fontFamily: 'inherit',
                      fontSize: 'var(--text-xs)',
                      padding: '2px 8px',
                    }}
                  >click to use</button>
                </li>
              )}
            </ul>
            Avoid <code className="mono">localhost</code>: inside the container it means subarr itself.
          </div>
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
      <h1 className="display" style={{ margin: 0, fontSize: 'var(--text-display-lg)', fontWeight: 600, letterSpacing: '-0.01em' }}>
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
            which is dramatically slower (think 30-60 min/episode instead of 1-2 min).
          </p>
          {/* #145: failure-mode guidance. Most "no GPU" reports in homelabs are
              actually misconfigured passthrough, not missing hardware. Give the
              user a checklist they can paste into their compose file. */}
          <div style={{ marginLeft: 18, marginTop: 14, fontSize: 'var(--text-sm)', color: 'var(--fg-1)' }}>
            <div style={{ fontWeight: 600, marginBottom: 6 }}>If you DO have an NVIDIA GPU, check:</div>
            <ol style={{ margin: 0, paddingLeft: 18, lineHeight: 1.6, color: 'var(--fg-2)' }}>
              <li>
                Host has the NVIDIA driver installed — run <code className="mono">nvidia-smi</code> on
                the host (not in a container) and confirm it returns a card.
              </li>
              <li>
                <code className="mono">nvidia-container-toolkit</code> is installed and registered with
                Docker (run <code className="mono">sudo nvidia-ctk runtime configure --runtime=docker</code>
                {' '}then restart the Docker daemon).
              </li>
              <li>
                Your subgen <code className="mono">compose.yaml</code> declares the GPU under
                {' '}<code className="mono">deploy.resources.reservations.devices</code> with
                {' '}<code className="mono">driver: nvidia</code>, <code className="mono">count: 1</code>, and
                {' '}<code className="mono">capabilities: [gpu]</code>.
              </li>
              <li>
                The subgen container is using a CUDA-enabled image (mccloud/subgen:latest ships with
                CUDA pre-baked).
              </li>
            </ol>
            <p style={{ margin: '10px 0 0', fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>
              You can continue without resolving this — subgen will fall back to CPU automatically.
              Revisit GPU setup later via the Subgen panel in Settings.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}


function StepWalk({ progress, setField, walkResult, onStart, isStarting }) {
  // #146: keep probe_roots in component state because the parent stores it
  // as an array; we render it as a comma-separated string for editing and
  // parse on every keystroke. Previously the onChange was a TODO no-op
  // so the user literally couldn't edit this field.
  const initial = (progress.probe_roots && progress.probe_roots.length)
    ? progress.probe_roots.join(', ')
    : 'TV, Movies';
  const [rootsText, setRootsText] = React.useState(initial);
  const handleRootsChange = (v) => {
    setRootsText(v);
    const parsed = v.split(',').map((s) => s.trim()).filter(Boolean);
    setField('probe_roots', parsed);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div>
        <h1 className="display" style={{ margin: 0, fontSize: 'var(--text-display-lg)', fontWeight: 600, letterSpacing: '-0.01em' }}>
          First walk
        </h1>
        <p style={{ margin: '8px 0 0', fontSize: 'var(--text-md)', color: 'var(--fg-1)', lineHeight: 1.55, maxWidth: 540 }}>
          Kicks off the initial probe walk against your library root(s). Discovers
          existing media files + their embedded subtitle tracks, so the dashboard
          shows real coverage on first paint instead of an empty state.
        </p>
      </div>
      <FormRow label="Probe roots"
        hint={`subfolders of ${progress.media_root || '/media/library'}, comma-separated`}>
        <TextInput
          value={rootsText}
          onChange={handleRootsChange}
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
            <span style={{ fontWeight: 600 }}>{walkResult.walks.length} walk(s) started</span>
          </div>
          <div style={{ paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 2 }}>
            {walkResult.walks.map((w, i) => (
              <span key={i} className="mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
                {w.walk_id ? `${w.walk_id.slice(0, 8)}… → ${w.root}` : `${w.root}: ${w.error}`}
              </span>
            ))}
          </div>
          {/* #146: be concrete about what "background" means. The
              walks run inside subarr's process, NOT the wizard. The
              user can finish onboarding and go straight to using the
              app; progress shows up on Library + Coverage tabs as
              walks complete. */}
          <p style={{ margin: '12px 0 0 18px', fontSize: 'var(--text-xs)', color: 'var(--fg-2)', lineHeight: 1.5 }}>
            Running in the background — you can hit <b>Finish</b> now and watch progress
            land on the Library + Coverage pages as each walk completes. Big libraries
            can take 10-30 minutes; nothing breaks if you close the tab.
          </p>
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
  const [autoDetectError, setAutoDetectError] = useState(null);
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
    setAutoDetectError(null);
    const r = await Api.autoDetect();
    setBusy(false);
    if (!r.available) {
      // #129: surface the actual reason in the welcome step instead of
      // failing silently. testResult only renders on integration steps,
      // so for the welcome-step button we need a dedicated slot.
      setAutoDetectError(r.reason || 'auto-detect unavailable (no docker socket / socket-proxy mounted)');
      return;
    }
    const services = r.services || {};
    const patch = {};
    // #137: stash the per-service detected metadata (container_name, etc.)
    // onto progress.detected_services so later integration steps can
    // surface a one-click "use detected container name" chip even if the
    // user didn't accept the inferred URL on first scan.
    const detected = {};
    for (const [svc, info] of Object.entries(services)) {
      if (info.candidate?.inferred_url) patch[`${svc}_url`] = info.candidate.inferred_url;
      if (info.candidate?.container_name) {
        detected[svc] = { container_name: info.candidate.container_name };
      }
      // We never autofill API keys here — wizard surfaces them per-service
      // with a clear "extracted from <source>" badge.
    }
    patch.detected_services = detected;
    setAutoDetected(Object.keys(services).length);
    setState(prev => ({ ...prev, progress: { ...prev.progress, ...patch } }));
  };

  const onTest = async () => {
    const step = STEPS[state.step];
    if (!step.service) return;
    setBusy(true);
    // #142 fix: clear the test result while testing — busy spinner conveys
    // the pending state. Previous code set `ok: false` which made the chip
    // flash red for half a second before the actual result arrived.
    setTestResult(null);
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
    if (step.id === 'welcome') return <StepWelcome onAutoDetect={onAutoDetect} detectedCount={autoDetected} autoDetectError={autoDetectError} />;
    if (step.id === 'paths')   return <StepPaths progress={state.progress} setField={setField} probeResult={probeResult} onProbe={onProbe} />;
    if (step.service)          return <StepIntegration step={step} progress={state.progress} setField={setField} testResult={testResult} onTest={onTest} isTesting={busy} />;
    if (step.id === 'gpu')     return <StepGpu gpuInfo={gpuInfo} />;
    if (step.id === 'walk')    return <StepWalk progress={state.progress} setField={setField} walkResult={walkResult} onStart={onStartWalk} isStarting={busy} />;
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

