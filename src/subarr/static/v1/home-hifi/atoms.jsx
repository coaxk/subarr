// Shared atoms for the hi-fi mockup.

const { useMemo, useState, useEffect } = React;

// ─── Wordmark ────────────────────────────────────────────────────
function Wordmark({ size = 18 }) {
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
function ProbeMark({ size = 20, color }) {
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
function Sparkline({ data, width = 80, height = 22, color, fill, stroke = 1.4 }) {
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
function Delta({ value, suffix = '/h' }) {
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
function StatusDot({ kind = 'ok', pulse, size }) {
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
function Glyph({ char, size = 14, color }) {
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
function fmtTime(d) {
  const h = String(d.getHours()).padStart(2, '0');
  const m = String(d.getMinutes()).padStart(2, '0');
  const s = String(d.getSeconds()).padStart(2, '0');
  return `${h}:${m}:${s}`;
}

// Demo data store — generated once, used everywhere.
function genSpark(n, base, vol) {
  return Array.from({ length: n }, (_, i) => {
    const t = i / n;
    return Math.max(0, base + Math.sin(t * 6) * vol * 0.4 + (Math.random() - 0.5) * vol);
  });
}

Object.assign(window, { Wordmark, ProbeMark, Sparkline, Delta, StatusDot, Glyph, fmtTime, genSpark });
