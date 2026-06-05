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
export function Sparkline({ data, width = 80, height = 22, color, fill, stroke = 1.4, responsive = false }) {
  // responsive: scale to the container's width (height stays fixed) via
  // viewBox. Used by the GPU util graph so it fills its flexible widget.
  // Default-off keeps every existing fixed-width caller (stage tiles, GPU
  // stats) pixel-identical.
  const svgProps = responsive
    ? { width: '100%', height, viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: 'none' }
    : { width, height };
  // Empty data → render an empty svg without path elements. Otherwise
  // the d attribute becomes a malformed string and Chromium logs
  // 'Expected moveto path command' to the console (caught by the
  // Playwright smoke suite). Live data returns spark=[] until we
  // wire sparkline history in v1.1.
  const c = color || 'var(--violet-500)';
  if (!data || data.length === 0) {
    return <svg {...svgProps} style={{ display: 'block' }} />;
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
    <svg {...svgProps} style={{ display: 'block' }}>
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

// ─── Language tags (#147) ────────────────────────────────────────
// Render a language code as a small flag + code. IMPORTANT: a flag is
// DECORATION, not a geography claim — languages are not countries. The
// tooltip always names the LANGUAGE. Multi-country languages (en, es,
// ar, pt …) intentionally get NO flag — we keep the bare code rather
// than pick a misleading single country. No flag → just the code.
//
// `name` drives the tooltip; `flag` is optional decoration. Keyed by
// ISO-639-1; LANG_ALIAS folds 3-letter codes + full names onto it.
export const LANG_INFO = Object.freeze({
  ko: { name: 'Korean', flag: '🇰🇷' },
  ja: { name: 'Japanese', flag: '🇯🇵' },
  zh: { name: 'Chinese', flag: '🇨🇳' },
  he: { name: 'Hebrew', flag: '🇮🇱' },
  ru: { name: 'Russian', flag: '🇷🇺' },
  fr: { name: 'French', flag: '🇫🇷' },
  de: { name: 'German', flag: '🇩🇪' },
  it: { name: 'Italian', flag: '🇮🇹' },
  hi: { name: 'Hindi', flag: '🇮🇳' },
  tr: { name: 'Turkish', flag: '🇹🇷' },
  nl: { name: 'Dutch', flag: '🇳🇱' },
  pl: { name: 'Polish', flag: '🇵🇱' },
  sv: { name: 'Swedish', flag: '🇸🇪' },
  no: { name: 'Norwegian', flag: '🇳🇴' },
  nb: { name: 'Norwegian Bokmål', flag: '🇳🇴' },
  nn: { name: 'Norwegian Nynorsk', flag: '🇳🇴' },
  da: { name: 'Danish', flag: '🇩🇰' },
  fi: { name: 'Finnish', flag: '🇫🇮' },
  cs: { name: 'Czech', flag: '🇨🇿' },
  el: { name: 'Greek', flag: '🇬🇷' },
  th: { name: 'Thai', flag: '🇹🇭' },
  vi: { name: 'Vietnamese', flag: '🇻🇳' },
  id: { name: 'Indonesian', flag: '🇮🇩' },
  uk: { name: 'Ukrainian', flag: '🇺🇦' },
  ro: { name: 'Romanian', flag: '🇷🇴' },
  hu: { name: 'Hungarian', flag: '🇭🇺' },
  fa: { name: 'Persian', flag: '🇮🇷' },
  is: { name: 'Icelandic', flag: '🇮🇸' },
  // Multi-country — name only, no flag (decoration would mislead):
  en: { name: 'English' },
  es: { name: 'Spanish' },
  pt: { name: 'Portuguese' },
  ar: { name: 'Arabic' },
  und: { name: 'Undetermined' },
});

// 3-letter (ISO-639-2/B + /T) and full-name aliases → ISO-639-1.
export const LANG_ALIAS = Object.freeze({
  kor: 'ko', korean: 'ko',
  jpn: 'ja', japanese: 'ja',
  zho: 'zh', chi: 'zh', chinese: 'zh', mandarin: 'zh',
  heb: 'he', hebrew: 'he',
  rus: 'ru', russian: 'ru',
  fre: 'fr', fra: 'fr', french: 'fr',
  ger: 'de', deu: 'de', german: 'de',
  ita: 'it', italian: 'it',
  hin: 'hi', hindi: 'hi',
  tur: 'tr', turkish: 'tr',
  dut: 'nl', nld: 'nl', dutch: 'nl',
  pol: 'pl', polish: 'pl',
  swe: 'sv', swedish: 'sv',
  nor: 'no', norwegian: 'no', nob: 'nb', nno: 'nn',
  dan: 'da', danish: 'da',
  fin: 'fi', finnish: 'fi',
  cze: 'cs', ces: 'cs', czech: 'cs',
  gre: 'el', ell: 'el', greek: 'el',
  tha: 'th', thai: 'th',
  vie: 'vi', vietnamese: 'vi',
  ind: 'id', indonesian: 'id',
  ukr: 'uk', ukrainian: 'uk',
  rum: 'ro', ron: 'ro', romanian: 'ro',
  hun: 'hu', hungarian: 'hu',
  per: 'fa', fas: 'fa', persian: 'fa', farsi: 'fa',
  ice: 'is', isl: 'is', icelandic: 'is',
  eng: 'en', english: 'en',
  spa: 'es', spanish: 'es', castilian: 'es',
  por: 'pt', portuguese: 'pt',
  ara: 'ar', arabic: 'ar',
  und: 'und', unknown: 'und',
});

// Normalize any code/name to ISO-639-1 (mirrors backend langs.normalize_lang).
export function normalizeLang(value) {
  if (!value) return '';
  const v = String(value).trim().toLowerCase();
  if (LANG_INFO[v]) return v;
  if (LANG_ALIAS[v]) return LANG_ALIAS[v];
  // bare 2-letter passthrough even if not in our map
  return v.length === 2 ? v : v;
}

export function langName(value) {
  const code = normalizeLang(value);
  return (LANG_INFO[code] && LANG_INFO[code].name) || (value ? String(value).toUpperCase() : '');
}

// LangTag — flag (decoration, when representative) + code, language tooltip.
export function LangTag({ value, size = 12, showCode = true, style }) {
  if (!value) return null;
  const code = normalizeLang(value);
  const info = LANG_INFO[code];
  const flag = info && info.flag;
  const name = (info && info.name) || String(value).toUpperCase();
  const label = code ? code.toUpperCase() : String(value).toUpperCase();
  return (
    <span
      title={name}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 4,
        fontSize: size, lineHeight: 1, whiteSpace: 'nowrap', ...style,
      }}
    >
      {flag && <span aria-hidden style={{ fontSize: size + 2 }}>{flag}</span>}
      {(showCode || !flag) && (
        <span style={{ fontFamily: 'var(--font-mono, monospace)', letterSpacing: '0.02em' }}>
          {label}
        </span>
      )}
    </span>
  );
}

