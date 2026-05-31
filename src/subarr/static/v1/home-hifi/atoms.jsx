// Shared atoms for the hi-fi mockup.
//
// React + ReactDOM come from the CDN <script> tags in the HTML — they
// resolve as runtime globals, no import needed. esbuild's IIFE format
// leaves the unresolved `React` identifier as a global ref.

const { useMemo, useState, useEffect } = React;

// ─── Wordmark ────────────────────────────────────────────────────
export function Wordmark({ size = 18 }) {
  // [·] glyph — probe bracket — plus the wordmark.
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <ProbeMark size={size + 2} />
      <span style={{
        fontFamily: 'var(--font-display)',
        fontSize: size,
        fontWeight: 700,
        letterSpacing: '-0.01em',
        lineHeight: 1,
      }}>
        <span style={{ color: 'var(--violet-500)' }}>sub</span>arr
      </span>
    </div>
  );
}

// Logo Option A from the brief — probe bracket "[·]" with a centred dot.
export function ProbeMark({ size = 20, color }) {
  const c = color || 'var(--violet-500)';
  const stroke = 1.8;
  // viewBox 24x24
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" style={{ display: 'block' }}>
      {/* left bracket */}
      <path d="M8 5 L4 5 L4 19 L8 19" fill="none" stroke={c} strokeWidth={stroke} strokeLinecap="square" strokeLinejoin="miter" />
      {/* right bracket */}
      <path d="M16 5 L20 5 L20 19 L16 19" fill="none" stroke={c} strokeWidth={stroke} strokeLinecap="square" strokeLinejoin="miter" />
      {/* centre dot */}
      <circle cx="12" cy="12" r="2" fill={c} />
    </svg>
  );
}

// ─── Sparkline ───────────────────────────────────────────────────
export function Sparkline({ data, width = 80, height = 22, color, fill, stroke = 1.4 }) {
  // Empty data → render an empty svg without path elements. Otherwise
  // the d attribute becomes a malformed string and Chromium logs
  // 'Expected moveto path command' to the console (caught by the
  // Playwright smoke suite). Live data returns spark=[] until we
  // wire sparkline history in v1.1.
  const c = color || 'var(--violet-500)';
  if (!data || data.length === 0) {
    return <svg width={width} height={height} style={{ display: 'block' }} />;
  }
  const max = Math.max(...data, 1);
  const min = Math.min(...data, 0);
  const range = max - min || 1;
  const step = data.length > 1 ? width / (data.length - 1) : width;
  const points = data.map((v, i) => {
    const x = i * step;
    const y = height - ((v - min) / range) * (height - 2) - 1;
    return [x, y];
  });
  const d = points.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');
  const areaD = fill ? d + ` L ${width} ${height} L 0 ${height} Z` : null;
  return (
    <svg width={width} height={height} style={{ display: 'block' }}>
      {areaD && <path d={areaD} fill={fill} opacity="0.18" />}
      <path d={d} fill="none" stroke={c} strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// ─── Delta indicator ─────────────────────────────────────────────
export function Delta({ value, suffix = '/h' }) {
  const n = Number(value);
  const isUp = n > 0;
  const isFlat = n === 0;
  const color = isFlat ? 'var(--fg-3)' : (isUp ? 'var(--success-500)' : 'var(--fg-2)');
  const sign = isFlat ? '·' : (isUp ? '↑' : '↓');
  return (
    <span className="num" style={{
      fontSize: 'var(--text-xs)',
      color,
      fontVariantNumeric: 'tabular-nums',
    }}>
      {sign} {Math.abs(n)}{suffix}
    </span>
  );
}

// ─── Status dot with optional pulse ──────────────────────────────
export function StatusDot({ kind = 'ok', pulse, size }) {
  const cls = `dot ${size === 'lg' ? 'lg' : ''} ${kind}`.trim();
  if (!pulse) return <span className={cls} />;
  return (
    <span style={{ position: 'relative', display: 'inline-flex', width: size === 'lg' ? 8 : 6, height: size === 'lg' ? 8 : 6 }}>
      <span className={cls} style={{ position: 'absolute', inset: 0 }} />
      <span className={cls} style={{ position: 'absolute', inset: 0, opacity: 0.5, animation: 'pulse 1.6s ease-out infinite' }} />
    </span>
  );
}

// ─── Tiny icon glyph (no real icons in mockup — just mono char) ──
export function Glyph({ char, size = 14, color }) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      width: size, height: size,
      fontFamily: 'var(--font-mono)',
      fontSize: size - 2,
      color: color || 'var(--fg-2)',
      lineHeight: 1,
    }}>{char}</span>
  );
}

// ─── Time string helpers ─────────────────────────────────────────
export function fmtTime(d) {
  const h = String(d.getHours()).padStart(2, '0');
  const m = String(d.getMinutes()).padStart(2, '0');
  const s = String(d.getSeconds()).padStart(2, '0');
  return `${h}:${m}:${s}`;
}

// ─── Icon kit: semantic → emoji map (#172) ─────────────────────
//
// One source of truth for the emoji glyphs we use as inline icons.
// Pages were drifting (settings ⚙ vs gear, leaderboard 📊 vs chart,
// etc.) and the same semantic ended up with different glyphs on
// different pages. Importing ICONS.X by intent rather than typing
// the literal keeps everything in sync and makes a swap a one-line
// change here.
//
// Rules:
//   - One semantic = one glyph. Don't add a second key for the same
//     concept ("gear" + "settings" — pick one).
//   - Use the semantic key in JSX, not the emoji literal: {ICONS.settings}.
//   - Decorative emoji inside copy strings can stay inline (a one-off
//     🎉 in a celebratory message is not a UI icon, it's content).
export const ICONS = Object.freeze({
  settings:    '⚙',
  leaderboard: '📊',
  warning:     '⚠',
  verified:    '✓',
  listen:      '🎧',
  whisper:     '🤖',
  celebrate:   '🎉',
  close:       '✕',
  edit:        '✎',
  save:        '💾',
  refresh:     '🔁',
  plug:        '🔌',
  folder:      '🗂',
  target:      '🎯',
  brain:       '🧠',
  llama:       '🦙',
  build:       '🛠',
});

// ─── SectionCard: panel + label header + optional action slot (#213) ──
//
// The canonical "labelled panel" shape used across settings, queue,
// coverage, review. Local copies drifted on padding (16/18 vs 12/16)
// and gap (10/12/14) — promoting to a single atom locks the rhythm
// so a designer change is one edit, not a grep across 5 files.
//
// Caller passes label (string or node), optional action (rendered
// right-aligned in the header — refresh button, status chip, etc.),
// and children (the actual content). Use the className escape hatch
// if a page needs extra layout flags (e.g. flex:1 to grow).
export function SectionCard({ label, children, action, className, style }) {
  return (
    <section className={className} style={{
      background: 'var(--bg-1)',
      border: 'var(--border)',
      borderRadius: 'var(--radius-lg)',
      padding: '16px 18px',
      display: 'flex', flexDirection: 'column', gap: 14,
      ...style,
    }}>
      <div style={{ display: 'flex', alignItems: 'center' }}>
        {typeof label === 'string'
          ? <span className="label">{label}</span>
          : label}
        <span style={{ flex: 1 }} />
        {action}
      </div>
      {children}
    </section>
  );
}

// ─── AsyncState: shared loading / error / empty render (#214) ────
//
// Five pages render the same loading / error / empty pattern with
// drifting copy and inconsistent padding. This atom consolidates the
// state machine so every page picks the right state from the same
// precedence + shows the same visual language:
//
//   - loading wins over everything (first-paint spinner).
//   - error wins over empty (we have data, just can't refresh).
//   - empty renders when there's no error and no rows.
//   - otherwise → render children unchanged.
//
// Pages that distinguish "first paint" from "background refetch"
// (see #225) should pass loading=isInitialLoad, not raw `loading` —
// the atom doesn't know about staleness.
//
// All three states render at the same padding + colour so a page
// flipping between them doesn't jump.
export function AsyncState({
  loading,
  error,
  empty,
  loadingMessage = 'Loading…',
  emptyMessage = 'Nothing here yet.',
  errorPrefix = "Couldn't load",
  children,
}) {
  if (loading) {
    return (
      <div role="status" aria-live="polite" style={{
        padding: 32, textAlign: 'center',
        color: 'var(--fg-2)', fontSize: 'var(--text-sm)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10,
      }}>
        <span className="spinner-ring" aria-hidden="true" />
        <span>{loadingMessage}</span>
      </div>
    );
  }
  if (error) {
    const msg = error && (error.message || String(error));
    return (
      <div role="alert" style={{
        padding: 32, textAlign: 'center',
        color: 'var(--error-500)', fontSize: 'var(--text-sm)',
      }}>
        {errorPrefix}: {msg || 'unknown error'}
      </div>
    );
  }
  if (empty) {
    return (
      <div style={{
        padding: 24, textAlign: 'center',
        color: 'var(--fg-3)', fontSize: 'var(--text-sm)',
      }}>
        {emptyMessage}
      </div>
    );
  }
  return children == null ? null : children;
}

// Demo data store — generated once, used everywhere.
export function genSpark(n, base, vol) {
  return Array.from({ length: n }, (_, i) => {
    const t = i / n;
    return Math.max(0, base + Math.sin(t * 6) * vol * 0.4 + (Math.random() - 0.5) * vol);
  });
}

