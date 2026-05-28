// Onboarding — 10-step wizard. Currently showing step 4 (Bazarr).

const ONBOARD_STEPS = [
  { id: 'welcome',  label: 'Welcome' },
  { id: 'paths',    label: 'Library paths' },
  { id: 'sonarr',   label: 'Sonarr' },
  { id: 'bazarr',   label: 'Bazarr' },
  { id: 'radarr',   label: 'Radarr' },
  { id: 'tautulli', label: 'Tautulli' },
  { id: 'subgen',   label: 'subgen' },
  { id: 'rules',    label: 'First rule' },
  { id: 'review',   label: 'Review' },
  { id: 'sync',     label: 'First sync' },
];

// ─── Stepper ─────────────────────────────────────────────────────
function Stepper({ current = 3 }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 0 }}>
      {ONBOARD_STEPS.map((s, i) => {
        const done = i < current;
        const active = i === current;
        const dotSize = active ? 24 : 18;
        return (
          <React.Fragment key={s.id}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, minWidth: 22 }}>
              <div style={{
                width: dotSize, height: dotSize,
                borderRadius: '50%',
                border: `1.5px solid ${active ? 'var(--violet-500)' : done ? 'var(--violet-500)' : 'var(--bg-4)'}`,
                background: done ? 'var(--violet-500)' : active ? 'var(--bg-0)' : 'transparent',
                color: done ? '#fff' : active ? 'var(--violet-400)' : 'var(--fg-3)',
                fontSize: active ? 11 : 10,
                fontWeight: 600,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontFamily: 'var(--font-mono)',
                transition: 'all var(--dur-base) var(--ease-out)',
              }}>
                {done ? '✓' : i + 1}
              </div>
            </div>
            {i < ONBOARD_STEPS.length - 1 && (
              <div style={{
                flex: 1,
                height: 1.5,
                background: i < current ? 'var(--violet-500)' : 'var(--bg-4)',
                margin: '0 4px',
              }} />
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
}

// ─── Form row ────────────────────────────────────────────────────
function FormRow({ label, value, hint, mono = true, focused, suffix }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-1)', fontWeight: 500 }}>{label}</span>
        {hint && <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>{hint}</span>}
      </div>
      <div style={{
        display: 'flex', alignItems: 'center',
        height: 34,
        padding: '0 12px',
        background: 'var(--bg-2)',
        border: `1px solid ${focused ? 'var(--violet-500)' : 'var(--bg-4)'}`,
        boxShadow: focused ? '0 0 0 3px rgba(139,92,246,0.18)' : 'none',
        borderRadius: 'var(--radius-md)',
        transition: 'border-color var(--dur-fast), box-shadow var(--dur-fast)',
      }}>
        <span className={mono ? 'mono' : ''} style={{
          flex: 1, fontSize: 'var(--text-md)',
          color: 'var(--fg-0)',
        }}>{value}</span>
        {suffix}
      </div>
    </label>
  );
}

// ─── Test connection result ──────────────────────────────────────
function TestResultOk() {
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 6,
      padding: '12px 14px',
      background: 'rgba(52,211,153,0.06)',
      border: '1px solid rgba(52,211,153,0.32)',
      borderRadius: 'var(--radius-md)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <StatusDot kind="ok" size="lg" />
        <span style={{ fontSize: 'var(--text-md)', fontWeight: 600, color: 'var(--fg-0)' }}>
          Bazarr 1.5.6 · 658 wanted · 12 providers configured
        </span>
      </div>
      <div style={{ paddingLeft: 18, display: 'flex', gap: 14 }}>
        <span className="mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>round-trip 84ms</span>
        <span className="mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>auth: ok</span>
        <span className="mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>write-back: ok</span>
      </div>
      <div style={{ paddingLeft: 18, marginTop: 4 }}>
        <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>
          GET /api/system/status → 200 · GET /api/episodes/wanted → 200 · POST /api/subtitles → 200
        </span>
      </div>
    </div>
  );
}

// ─── Step body — Bazarr ──────────────────────────────────────────
function StepBazarr() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
          <span className="label" style={{ color: 'var(--violet-400)', letterSpacing: '0.14em' }}>step 4 of 10</span>
          <span style={{ width: 12, height: 1, background: 'var(--bg-4)' }} />
          <span className="label" style={{ color: 'var(--fg-3)' }}>integrations</span>
        </div>
        <h1 className="display" style={{
          margin: 0,
          fontSize: 26, lineHeight: 1.15,
          fontWeight: 600,
          letterSpacing: '-0.01em',
        }}>Connect Bazarr</h1>
        <p style={{
          margin: '8px 0 0',
          fontSize: 'var(--text-md)',
          color: 'var(--fg-1)',
          lineHeight: 1.55,
          maxWidth: 540,
        }}>
          Subarr reads Bazarr's wanted lists and writes completed subtitles back through Bazarr so existing providers and language profiles still apply.
          Find the URL and API key in Bazarr Settings → General.
        </p>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <FormRow label="URL" hint="reachable from this container" value="http://bazarr:6767" focused />
        <FormRow label="API key" hint="from Bazarr Settings → General" value="••••••••••••••••a4f2" suffix={
          <button style={{
            fontSize: 'var(--text-xs)',
            color: 'var(--fg-2)',
            padding: '2px 6px',
            border: '1px solid var(--bg-4)',
            borderRadius: 2,
            background: 'var(--bg-3)',
          }}>Reveal</button>
        } />
      </div>

      <TestResultOk />
    </div>
  );
}

// ─── Footer actions ──────────────────────────────────────────────
function WizardFooter({ canContinue = true }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10,
      paddingTop: 18,
      borderTop: 'var(--border)',
    }}>
      <button className="btn ghost">← Back</button>
      <span style={{ flex: 1 }} />
      <button className="btn">Test connection</button>
      <button className="btn primary" style={{ opacity: canContinue ? 1 : 0.5, pointerEvents: canContinue ? 'auto' : 'none' }}>
        Continue →
      </button>
    </div>
  );
}

// ─── Page wrapper ────────────────────────────────────────────────
function OnboardingPage() {
  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex', flexDirection: 'column',
      background: 'var(--bg-0)',
    }}>
      {/* Slim header (just wordmark + tiny meta) */}
      <header style={{
        height: 56,
        display: 'flex', alignItems: 'center',
        padding: '0 var(--space-6)',
      }}>
        <Wordmark size={18} />
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>
          First-run setup · est. 4 minutes
        </span>
      </header>

      <div style={{
        flex: 1,
        display: 'flex', justifyContent: 'center', alignItems: 'flex-start',
        padding: '20px 24px 60px',
      }}>
        <div style={{ width: '100%', maxWidth: 620, display: 'flex', flexDirection: 'column', gap: 26 }}>

          {/* Stepper */}
          <div>
            <Stepper current={3} />
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 10 }}>
              <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)', letterSpacing: '0.04em' }}>
                3 done · 1 in progress · 6 left
              </span>
              <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--violet-400)', letterSpacing: '0.04em' }}>
                bazarr
              </span>
            </div>
          </div>

          {/* Card */}
          <div style={{
            background: 'var(--bg-1)',
            border: 'var(--border)',
            borderRadius: 'var(--radius-lg)',
            padding: '26px 28px 22px',
            display: 'flex', flexDirection: 'column', gap: 22,
          }}>
            <StepBazarr />
            <WizardFooter canContinue />
          </div>

          {/* Skip link */}
          <div style={{ textAlign: 'center' }}>
            <a href="#" style={{
              fontSize: 'var(--text-sm)',
              color: 'var(--fg-3)',
              textDecoration: 'none',
              borderBottom: '1px dashed var(--bg-5)',
              paddingBottom: 1,
            }}>Skip — connect Bazarr later in Settings</a>
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { OnboardingPage });
