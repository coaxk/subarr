// #131 — Tuning Lab: drive a subgen config sweep against the live model and
// rank the outputs with the validated tournament judge.
//
// Flow: pick a media file + choose configs to compare (curated recipes and/or
// custom kwargs) → POST /api/arena/run → stream /events (SSE) for live
// progress → render the ranked scorecard. Rides subgen's v4.10 /asr path-input
// channel: no upload, nothing written to the library.
//
// Styling note: uses the home-hifi token system (--bg-*, --fg-*, --border,
// --radius-lg, --success-500, --violet-500) so the page matches the rest of
// the app — this is the marquee feature, not a bolt-on.

import { SectionCard, StatusDot, Glyph, ICONS, LangTag } from './atoms.jsx';
import { fetchBrowse } from './library.jsx';  // #145: reuse Library's cached, rollup-skipping browse

const { useState, useEffect, useRef, useCallback } = React;

// Curated recipes the user toggles into a sweep — the validated Tier-B
// families. CRITICAL: each non-default recipe is a FULL, EXPLICIT config (every
// discriminating knob set), NOT a partial override. Partial overrides collapse
// to near-identical output once your subgen base is already tuned (the old set
// had four recipes converge on a tuned base — a tie that was really an
// artifact). Full configs differ BY CONSTRUCTION regardless of your base, so a
// sweep always actually separates them. (Seed of the crowd-curated per-language
// set — see #124.)
const CURATED = [
  { id: 'default', label: 'default', kwargs: {},
    why: 'Your current subgen settings, unchanged. The baseline every other recipe has to beat.' },
  { id: 'clean-film', label: 'clean-film',
    kwargs: { vad_filter: false, condition_on_previous_text: true, beam_size: 5, best_of: 5, temperature: 0, compression_ratio_threshold: 2.4, no_speech_threshold: 0.6 },
    why: 'Tuned for clean studio audio (most films and TV). Keeps sentence context for natural phrasing and trusts the soundtrack — no aggressive silence gating that could clip soft dialogue.' },
  { id: 'noisy-robust', label: 'noisy-robust',
    kwargs: { vad_filter: true, condition_on_previous_text: false, beam_size: 5, best_of: 5, temperature: 0, compression_ratio_threshold: 2.2, no_speech_threshold: 0.7 },
    why: 'For music, effects-heavy, or low-quality audio. Gates out non-speech, drops line-to-line carry-over (so noise can’t snowball), and tightens the gibberish guard.' },
  { id: 'high-accuracy', label: 'high-accuracy',
    kwargs: { vad_filter: false, condition_on_previous_text: true, beam_size: 8, best_of: 8, patience: 2, temperature: 0, compression_ratio_threshold: 2.4, no_speech_threshold: 0.6 },
    why: 'Widest search with full context. The slowest pass, but squeezes out the most accurate read when quality matters more than speed.' },
  { id: 'fast-draft', label: 'fast-draft',
    kwargs: { vad_filter: false, condition_on_previous_text: true, beam_size: 1, best_of: 1, temperature: 0 },
    why: 'Quick single-pass greedy decode. Good for a fast sanity check or a rough draft — not for final quality.' },
  { id: 'raw-unfiltered', label: 'raw-unfiltered',
    kwargs: { vad_filter: false, condition_on_previous_text: true, temperature: 0, no_speech_threshold: 1.0, compression_ratio_threshold: 100 },
    why: 'All guardrails off — deliberately lets the model hallucinate on silence and music. A diagnostic canary (it should lose on the quiet clip), NOT a setting to adopt: it shows you how much the guards are doing.' },
];
// #142: pre-select all six so a sweep compares the full set by default (max data).
const DEFAULT_SELECTED = ['default', 'clean-film', 'noisy-robust', 'high-accuracy', 'fast-draft', 'raw-unfiltered'];

const KNOBS = [
  ['beam_size', 'a number, 1 to 10', 'How hard it searches for the best wording. Higher is more accurate but slower. Around 5 is a sensible top end.'],
  ['vad_filter', 'on or off', 'Skips silence and non-speech before transcribing. The single biggest win against invented lines over music or quiet.'],
  ['temperature', '0.0 to 1.0', 'How much it guesses. 0 is steady and usually best; it only climbs higher as a fallback.'],
  ['condition_on_previous_text', 'on or off', 'Whether each line is shaped by the one before it. Turn off when the output repeats or wanders.'],
  ['compression_ratio_threshold', 'a number near 2.4', 'Catches gibberish and looping. Lower is stricter about throwing out junk.'],
  ['initial_prompt', 'some text', 'Primes names, jargon and tone. Handy for niche or technical content.'],
];

// Spoken-language picklist (same vocabulary as the Review page). Value is the
// ISO code we send; the user only ever sees the name.
const LANGS = [
  ['ara', 'Arabic'], ['bul', 'Bulgarian'], ['chi', 'Chinese'], ['hrv', 'Croatian'], ['cze', 'Czech'],
  ['dan', 'Danish'], ['dut', 'Dutch'], ['eng', 'English'], ['fin', 'Finnish'], ['fre', 'French'],
  ['ger', 'German'], ['gre', 'Greek'], ['heb', 'Hebrew'], ['hin', 'Hindi'], ['hun', 'Hungarian'],
  ['ind', 'Indonesian'], ['ita', 'Italian'], ['jpn', 'Japanese'], ['kor', 'Korean'], ['nor', 'Norwegian'],
  ['pol', 'Polish'], ['por', 'Portuguese'], ['rum', 'Romanian'], ['rus', 'Russian'], ['srp', 'Serbian'],
  ['spa', 'Spanish'], ['swe', 'Swedish'], ['tha', 'Thai'], ['tur', 'Turkish'], ['ukr', 'Ukrainian'], ['vie', 'Vietnamese'],
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
  return <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-3)', marginTop: 4, lineHeight: 1.5 }}>{children}</div>;
}

function StatusPill({ status }) {
  const map = {
    pending: { kind: 'idle', text: 'Pending' }, queued: { kind: 'idle', text: 'Queued' },
    running: { kind: 'busy', text: 'Running' },
    done: { kind: 'ok', text: 'Done' }, error: { kind: 'err', text: 'Error' },
  };
  const s = map[status] || map.pending;
  return <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}><StatusDot kind={s.kind} pulse={status === 'running'} />{s.text}</span>;
}

// Collapsible card matching SectionCard styling. Remembers its open/closed
// state across navigation via localStorage (keyed by `id`).
function Collapsible({ label, id, defaultOpen = true, children }) {
  const key = `arena.collapse.${id}`;
  const [open, setOpen] = useState(() => {
    try { const v = localStorage.getItem(key); return v == null ? defaultOpen : v === '1'; } catch (e) { return defaultOpen; }
  });
  const toggle = () => setOpen((o) => { const n = !o; try { localStorage.setItem(key, n ? '1' : '0'); } catch (e) {} return n; });
  return (
    <section style={cardStyle}>
      <button onClick={toggle} aria-expanded={open}
              style={{ display: 'flex', alignItems: 'center', width: '100%', background: 'transparent', border: 'none', cursor: 'pointer', padding: 0 }}>
        <span className="label">{label}</span>
        <span style={{ flex: 1 }} />
        <span style={{ color: 'var(--fg-3)', fontSize: 'var(--text-sm)' }}>{open ? '▾' : '▸'}</span>
      </button>
      {open && <div style={{ marginTop: 14 }}>{children}</div>}
    </section>
  );
}

// A row of segments, one per recipe — green = done, pulsing = running, grey =
// pending. The compact live-progress indicator in the sweeps list.
function ProgressSegments({ done, total, running }) {
  return (
    <span style={{ display: 'inline-flex', gap: 3, flex: 'none' }} title={`${done} of ${total} done`}>
      {Array.from({ length: total }, (_, i) => {
        const state = i < done ? 'done' : (running && i === done ? 'run' : 'pend');
        return <span key={i} style={{
          width: 16, height: 6, borderRadius: 3,
          background: state === 'done' ? 'var(--success-500)' : state === 'run' ? 'var(--violet-500)' : 'var(--bg-4)',
          animation: state === 'run' ? 'pulse 1.6s ease-out infinite' : undefined,
        }} />;
      })}
    </span>
  );
}

// ── file picker modal ────────────────────────────────────────────────────────
const VIDEO_RE = /\.(mkv|mp4|avi|m4v|mov|webm|ts)$/i;

function FilePicker({ onPick, onPickMany, onClose, bulkReady = true }) {
  const [path, setPath] = useState('');
  const [entries, setEntries] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  // #141: multi-select — checkboxes accumulate across folders so you can
  // assemble a bulk batch from several shows, then queue a sweep for each.
  const [picked, setPicked] = useState(() => new Set());
  const togglePick = (p) => setPicked((s) => { const n = new Set(s); n.has(p) ? n.delete(p) : n.add(p); return n; });

  const scrollRef = React.useRef(null);

  useEffect(() => {
    let alive = true;
    setLoading(true); setErr(null);
    // #145: Library's cached browse — skips the 10s+ recursive rollup at
    // root and serves revisited folders instantly from browseCache.
    fetchBrowse(path)
      .then((d) => { if (alive) setEntries(d.entries || []); })
      .catch((e) => { if (alive) setErr(String(e)); }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [path]);

  const parts = path ? path.split('/') : [];
  const dirs = (entries || []).filter((e) => e.is_dir);
  const files = (entries || []).filter((e) => !e.is_dir && VIDEO_RE.test(e.name));

  // #145/#137: A–Z ladder — jump to the first folder/file starting with a
  // letter. Only worth showing once a level has enough entries to scroll.
  const ordered = [...dirs, ...files];
  const presentLetters = new Set(ordered.map((e) => (e.name[0] || '').toUpperCase()).filter((c) => c >= 'A' && c <= 'Z'));
  const showLadder = ordered.length > 20;
  const jumpTo = (letter) => {
    const el = scrollRef.current && scrollRef.current.querySelector(`[data-letter="${letter}"]`);
    if (el) el.scrollIntoView({ block: 'start' });
  };
  const ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('');
  const firstLetterSeen = new Set();
  const letterAttr = (name) => {
    const c = (name[0] || '').toUpperCase();
    if (c >= 'A' && c <= 'Z' && !firstLetterSeen.has(c)) { firstLetterSeen.add(c); return c; }
    return undefined;
  };

  return (
    <div style={modalBackdrop} onClick={onClose}>
      <div style={modalCard} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <strong style={{ fontSize: 'var(--text-lg)' }}>Pick a media file</strong>
          <button onClick={onClose} style={iconBtnStyle}><Glyph char={ICONS.close || '×'} size={14} /></button>
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, fontSize: 'var(--text-sm)', marginBottom: 10 }}>
          <button onClick={() => setPath('')} style={crumbStyle}>library</button>
          {parts.map((p, i) => (
            <span key={i} style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
              <span style={{ color: 'var(--fg-3)' }}>/</span>
              <button onClick={() => setPath(parts.slice(0, i + 1).join('/'))} style={crumbStyle}>{p}</button>
            </span>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <div ref={scrollRef} style={{ flex: 1, minWidth: 0, minHeight: 220, maxHeight: 420, overflow: 'auto', border: 'var(--border)', borderRadius: 'var(--radius-lg)' }}>
            {loading ? <div style={pickRowMuted}>Loading…</div>
              : err ? <div style={{ ...pickRowMuted, color: 'var(--error-500)' }}>Couldn’t browse: {err}</div>
              : (dirs.length === 0 && files.length === 0) ? <div style={pickRowMuted}>No folders or video files here.</div>
              : (<>
                  {dirs.map((e) => (
                    <button key={e.path} data-letter={letterAttr(e.name)} onClick={() => setPath(e.path)} style={pickRow}>
                      <span style={{ marginRight: 8 }}>📁</span>{e.name}
                      {e.video_count ? <span style={{ color: 'var(--fg-3)', marginLeft: 8, fontSize: 'var(--text-xs)' }}>{e.video_count} video{e.video_count === 1 ? '' : 's'}</span> : null}
                    </button>
                  ))}
                  {files.map((e) => (
                    <div key={e.path} data-letter={letterAttr(e.name)} style={{ display: 'flex', alignItems: 'center' }}>
                      <input type="checkbox" checked={picked.has(e.path)} onChange={() => togglePick(e.path)}
                             onClick={(ev) => ev.stopPropagation()} title="Add to bulk batch"
                             style={{ margin: '0 0 0 12px', flex: 'none', cursor: 'pointer', accentColor: 'var(--violet-500)' }} />
                      <button onClick={() => onPick(e.path)} style={{ ...pickRow, color: 'var(--fg-0)', flex: 1 }}>
                        <span style={{ marginRight: 8 }}>🎬</span>{e.name}
                      </button>
                    </div>
                  ))}
                </>)}
          </div>
          {showLadder && (
            <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 0, fontSize: 9, color: 'var(--fg-3)', userSelect: 'none', flex: 'none', width: 14 }}>
              {ALPHABET.map((c) => {
                const has = presentLetters.has(c);
                return (
                  <span key={c} onClick={has ? () => jumpTo(c) : undefined}
                        style={{ textAlign: 'center', lineHeight: '1.15', cursor: has ? 'pointer' : 'default',
                                 color: has ? 'var(--fg-2)' : 'var(--fg-4, #555)', fontWeight: has ? 600 : 400 }}>{c}</span>
                );
              })}
            </div>
          )}
        </div>
        {picked.size > 0 ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, marginTop: 10 }}>
            <span style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>{picked.size} file{picked.size === 1 ? '' : 's'} selected for bulk sweep</span>
            <span style={{ display: 'flex', gap: 8 }}>
              <button onClick={() => setPicked(new Set())} style={ghostBtnStyle}>Clear</button>
              <button onClick={() => bulkReady && onPickMany([...picked])} disabled={!bulkReady}
                      title={bulkReady ? '' : 'Select at least one recipe first'}
                      style={{ ...primaryBtnStyle, opacity: bulkReady ? 1 : 0.5, cursor: bulkReady ? 'pointer' : 'not-allowed' }}>
                Sweep {picked.size} selected →
              </button>
            </span>
          </div>
        ) : (
          <Hint>Click a file to sweep it, or tick several (across folders) and queue a sweep for each at once. A short clip with real dialogue compares recipes just as well as a whole episode, and finishes far faster.</Hint>
        )}
      </div>
    </div>
  );
}

// ── "what this is" ───────────────────────────────────────────────────────────
function WhatThisIs() {
  const steps = [
    ['1', 'Transcribe the source once', 'subgen transcribes your file in its own language. That transcript is the yardstick everything else is measured against.'],
    ['2', 'Run each recipe', 'Every recipe you choose runs as its own pass on the live model, producing one subtitle each.'],
    ['3', 'The judge ranks them', "subarr's tournament judge scores each result (invented lines, repetition, readability, and how faithful it is to the source) and crowns a winner."],
  ];
  return (
    <Collapsible label="What this is" id="what" defaultOpen>
      <p style={{ margin: '0 0 14px', color: 'var(--fg-1)', lineHeight: 1.6, fontSize: 'var(--text-base)' }}>
        Stop guessing which Whisper settings give the best subtitles for tricky content. Pick one file,
        choose a few recipes to compare, and let the judge tell you which one actually wins — objectively,
        on your own hardware and model.
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 12 }}>
        {steps.map(([n, title, body]) => (
          <div key={n} style={{ background: 'var(--bg-2)', border: 'var(--border)', borderRadius: 'var(--radius-lg)', padding: 14, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <span style={stepNum}>{n}</span><span style={{ fontWeight: 600, fontSize: 'var(--text-base)', color: 'var(--fg-0)' }}>{title}</span>
            </div>
            <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)', lineHeight: 1.55 }}>{body}</div>
          </div>
        ))}
      </div>
      <div style={expectStyle}>
        <b style={{ color: 'var(--fg-1)' }}>What to expect:</b> you just pick a file — subarr automatically cuts a short
        representative sample (mixing dialogue, a speech→silence edge, and a quiet/music stretch, because that quiet bit is
        exactly where bad settings start inventing lines). Recipes run on that sample, so a sweep takes a couple of minutes,
        not an hour. Progress shows live; you can leave and come back; nothing is written to your library.
      </div>
    </Collapsible>
  );
}

// ── cheat-sheet ──────────────────────────────────────────────────────────────
function KnobReference() {
  return (
    <Collapsible label="What the settings mean" id="settings" defaultOpen>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {KNOBS.map(([name, type, desc]) => (
          <div key={name} style={{ fontSize: 'var(--text-base)', lineHeight: 1.5 }}>
            <code style={codeName}>{name}</code>
            <span style={{ color: 'var(--fg-3)', marginLeft: 8, fontSize: 'var(--text-sm)' }}>{type}</span>
            <div style={{ color: 'var(--fg-2)', marginTop: 2, fontSize: 'var(--text-sm)' }}>{desc}</div>
          </div>
        ))}
        <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-3)' }}>
          The curated recipes below are ready-made combinations of these — just choose the ones you want to compare.
        </div>
      </div>
    </Collapsible>
  );
}

// ── configure sweep ──────────────────────────────────────────────────────────
function SweepForm({ onRun, disabled, gate }) {
  const [mediaPath, setMediaPath] = useState('');
  const [picking, setPicking] = useState(false);
  const [sourceLang, setSourceLang] = useState('');
  const [selected, setSelected] = useState(() => new Set(DEFAULT_SELECTED));
  const [custom, setCustom] = useState([]);

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

  const buildVariants = () => [
    ...chosen.map((c) => ({ label: c.label, kwargs: c.kwargs })),
    ...custom.map((c, i) => ({ label: c.label.trim(), kwargs: customParsed[i].value })),
  ];
  // recipes-ready (independent of the file field) — gates the bulk path
  const recipesReady = total >= 1 && !dupLabel && !badCustom;

  const submit = async () => {
    if (!ready) return;
    const ok = await onRun({ media_path: mediaPath.trim().replace(/^\/+/, ''), source_language: sourceLang || null, variants: buildVariants() });
    if (ok) setMediaPath('');  // keep recipe picks; clear the file so the next one is two clicks away
  };

  // #141: queue one sweep per selected file with the current recipes. The
  // backend concurrency cap serialises them, so N queued sweeps drain safely.
  const submitMany = async (paths) => {
    if (!recipesReady || !paths || !paths.length) return;
    const variants = buildVariants();
    setPicking(false);
    for (const p of paths) {
      await onRun({ media_path: p.replace(/^\/+/, ''), source_language: sourceLang || null, variants });
    }
    setMediaPath('');
  };

  return (
    <SectionCard label="Configure sweep">
      {picking && <FilePicker onClose={() => setPicking(false)} onPick={(p) => { setMediaPath(p); setPicking(false); }} onPickMany={submitMany} bulkReady={recipesReady} />}

      {/* media file */}
      <div style={fieldStyle}>
        <span style={lblStyle}>Media file</span>
        <div style={{ display: 'flex', gap: 8 }}>
          <input value={mediaPath} onChange={(e) => setMediaPath(e.target.value)} placeholder="Pick a file from your library…" style={{ ...inputStyle, flex: 1, minWidth: 0 }} />
          <button onClick={() => setPicking(true)} style={ghostBtnStyle}>Browse…</button>
        </div>
        <Hint>Pick any file — even a full episode. subarr samples a short representative clip from it automatically; you don't need to trim anything.</Hint>
      </div>

      {/* spoken language */}
      <div style={fieldStyle}>
        <span style={lblStyle}>Spoken language</span>
        <select value={sourceLang} onChange={(e) => setSourceLang(e.target.value)} style={{ ...inputStyle, maxWidth: 260, cursor: 'pointer' }}>
          <option value="">Auto-detect</option>
          {LANGS.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
        </select>
        <Hint>Leave on Auto-detect and Whisper figures it out. Choosing the language when you know it nudges accuracy and saves a step.</Hint>
      </div>

      {/* recipes */}
      <div style={fieldStyle}>
        <span style={lblStyle}>Recipes to compare</span>
        <Hint>Choose the curated recipes you want to trial — each one runs separately and competes against the others. Add your own at the bottom to test a specific tweak.</Hint>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 12, marginTop: 10 }}>
          {CURATED.map((c) => {
            const on = selected.has(c.id);
            return (
              <button key={c.id} onClick={() => toggle(c.id)} style={{
                textAlign: 'left', cursor: 'pointer', borderRadius: 'var(--radius-lg)', padding: 14, minWidth: 0,
                background: on ? 'rgba(52,211,153,0.08)' : 'var(--bg-2)',
                border: on ? '1px solid var(--success-500)' : 'var(--border)',
                transition: 'border-color var(--dur-base), background var(--dur-base)',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                  <span style={{
                    width: 18, height: 18, borderRadius: 'var(--radius-md)', flex: 'none',
                    border: on ? '1px solid var(--success-500)' : '1px solid var(--bg-5)',
                    background: on ? 'var(--success-500)' : 'transparent', color: 'var(--bg-0)',
                    fontSize: 12, fontWeight: 700, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                  }}>{on ? '✓' : ''}</span>
                  <span style={{ fontWeight: 600, fontSize: 'var(--text-md)', color: 'var(--fg-0)' }}>{c.label}</span>
                </div>
                <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)', lineHeight: 1.5 }}>{c.why}</div>
                <code style={{ display: 'block', marginTop: 8, fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)', color: 'var(--fg-3)', wordBreak: 'break-all', overflowWrap: 'anywhere' }}>
                  {JSON.stringify(c.kwargs)}
                </code>
              </button>
            );
          })}
        </div>

        {custom.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 12 }}>
            {custom.map((v, i) => (
              <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                <input value={v.label} onChange={(e) => setCustomRow(i, { label: e.target.value })} placeholder="label" style={{ ...inputStyle, width: 160, flex: 'none' }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <textarea value={v.kwargs} onChange={(e) => setCustomRow(i, { kwargs: e.target.value })} placeholder='{"beam_size": 5}' rows={1}
                            style={{ ...inputStyle, width: '100%', fontFamily: 'var(--font-mono)', resize: 'vertical', minHeight: 36, border: customParsed[i].ok ? 'var(--border)' : '1px solid var(--error-500)' }} />
                  {!customParsed[i].ok && <div style={{ color: 'var(--error-500)', fontSize: 'var(--text-sm)', marginTop: 2 }}>kwargs {customParsed[i].error}</div>}
                </div>
                <button onClick={() => removeCustom(i)} title="remove" style={iconBtnStyle}><Glyph char={ICONS.close || '×'} size={14} /></button>
              </div>
            ))}
          </div>
        )}
        <button onClick={addCustom} style={{ ...ghostBtnStyle, marginTop: 10 }}>+ Add custom recipe</button>
        {dupLabel && <div style={{ color: 'var(--error-500)', fontSize: 'var(--text-sm)', marginTop: 6 }}>Recipe names must be unique.</div>}
        {total === 0 && <div style={{ color: 'var(--fg-3)', fontSize: 'var(--text-sm)', marginTop: 6 }}>Choose at least one recipe to compare.</div>}
      </div>

      {gate && !gate.ok && (
        <div style={gateNoticeStyle}>{gate.reason}{gate.remedy && <div style={{ marginTop: 4, color: 'var(--fg-3)' }}>{gate.remedy}</div>}</div>
      )}

      <div style={{ borderTop: 'var(--border)', paddingTop: 16, marginTop: 2 }}>
        {total > 0 && (
          <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)', marginBottom: 10, lineHeight: 1.5 }}>
            This runs <b>{total}</b> recipe{total === 1 ? '' : 's'} separately (plus <b>1</b> pass to transcribe the source),
            so <b>{total + 1}</b> transcription{total + 1 === 1 ? '' : 's'} in total. You’ll get one ranked result per recipe.
          </div>
        )}
        <button onClick={submit} disabled={!ready} style={{ ...primaryBtnStyle, opacity: !ready ? 0.5 : 1, cursor: !ready ? 'not-allowed' : 'pointer' }}>
          {`Queue sweep${total ? ` · ${total} recipe${total === 1 ? '' : 's'}` : ''}`}
        </button>
      </div>
    </SectionCard>
  );
}

// ── results ──────────────────────────────────────────────────────────────────
const COL_HELP = {
  'Mean score': "The recipe's average overall score across all clips (0–100). Higher is better — this decides the top pick.",
  Faithfulness: 'Average meaning-match to the source across clips (0–1). Blank if the optional accuracy model isn’t installed.',
  'Clips won': 'How many strata clips this recipe topped. Winning ACROSS clips is what makes a pick trustworthy; winning one is luck.',
  Issues: 'Clips where this recipe produced unusable output (loops / hallucination / no subtitle).',
};

const CONF = {
  high: { color: 'var(--success-500)', text: 'high' },
  moderate: { color: 'var(--warn-500)', text: 'moderate' },
  low: { color: 'var(--error-500)', text: 'low' },
};

function ConfChip({ conf }) {
  const c = CONF[conf] || CONF.low;
  return <span style={{ fontSize: 'var(--text-xs)', color: c.color, border: `1px solid ${c.color}`, borderRadius: 'var(--radius-pill)', padding: '1px 8px', flex: 'none' }}>{c.text} confidence</span>;
}

// Live progress: each strata clip is judged separately, so show the clips.
function ClipProgress({ prog }) {
  const clips = prog.clips || [];
  const dot = (status) => ({
    width: 9, height: 9, borderRadius: '50%', flex: 'none',
    background: status === 'done' ? 'var(--success-500)' : status === 'running' ? 'var(--violet-500)' : 'var(--bg-4)',
    animation: status === 'running' ? 'pulse 1.6s ease-out infinite' : undefined,
  });
  return (
    <div style={{ marginBottom: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
      {clips.length === 0 && <div style={{ color: 'var(--fg-3)', fontSize: 'var(--text-sm)' }}>Sampling the file…</div>}
      {clips.map((c, i) => (
        <div key={i} style={progRow}>
          <span style={dot(c.status)} />
          <span style={{ color: 'var(--fg-1)' }}>{c.kind ? `${c.kind} clip` : `clip ${i + 1}`}</span>
          <span style={{ marginLeft: 'auto', color: 'var(--fg-3)', fontSize: 'var(--text-sm)' }}>{c.status}</span>
        </div>
      ))}
      {prog.total > 0 && (
        <div style={{ marginTop: 4, fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>
          {prog.done} of {prog.total} transcriptions — each clip is judged on its own, then results are combined.
        </div>
      )}
    </div>
  );
}

// One sweep's detail: live clip progress, then the AGGREGATED guidance + table
// + per-clip breakdown. Takes the full run object; null while loading.
function SweepDetail({ run }) {
  if (!run) return <div style={{ color: 'var(--fg-3)', fontSize: 'var(--text-sm)', padding: '4px 0' }}>Loading…</div>;
  const prog = run.outcomes && !Array.isArray(run.outcomes) ? run.outcomes : {};
  const res = run.result;
  const kwById = Object.fromEntries((run.variants || []).map((v) => [v.label, v.kwargs]));

  return (
    <div>
      {run.error && <div style={{ color: 'var(--error-500)', marginBottom: 10 }}>Error: {run.error}</div>}
      {run.status === 'running' && <ClipProgress prog={prog} />}

      {res && (res.aggregate || []).length > 0 ? (() => {
        const rows = res.aggregate;
        const winner = res.winner;
        const winnerKwargs = kwById[winner];
        const avoid = rows.filter((r) => r.disqualified_in > 0 || r.clips_scored === 0).map((r) => r.label);
        const nclips = (res.per_clip || []).length;
        const wonRow = rows.find((r) => r.label === winner);
        const tie = !!res.tie;   // computed in the backend so row + guidance + explainer all agree
        return (
          <>
            <div style={guidanceBoxStyle}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <span style={{ fontWeight: 700, color: 'var(--fg-0)' }}>Guidance</span>
                {res.confidence && !tie && <ConfChip conf={res.confidence} />}
              </div>
              {res.explanation && (
                <div style={explainStyle}>
                  <span style={{ marginRight: 6 }}>💡</span>{res.explanation}
                </div>
              )}
              {tie ? (
                <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>
                  These recipes performed <b>about the same</b> on this clip (within noise) — no clear advantage either way.
                  That usually means the audio is easy enough that settings don't matter much here. To actually separate
                  recipes, try a <b>harder clip</b> (foreign-language, noisy, or with a long music/quiet stretch) — that's where
                  weaker settings start to hallucinate or loop.
                </div>
              ) : winner ? (
                <>
                  <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>
                    <b style={{ color: 'var(--success-500)' }}>Top pick:</b> {winner}
                    {wonRow && <> — best average across the clips (won {wonRow.clips_won} of {nclips}).</>} To try it as your default:
                  </div>
                  <code style={winnerCode}>SUBGEN_KWARGS={JSON.stringify(winnerKwargs || {})}</code>
                </>
              ) : (
                <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)' }}>No clear pick — nothing produced usable output. Try a different clip or set the spoken language.</div>
              )}
              {avoid.length > 0 && (
                <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)', marginTop: 6 }}>
                  <b style={{ color: 'var(--error-500)' }}>Avoid:</b> {avoid.join(', ')} — unusable on at least one clip (loops / hallucination / no subtitle).
                </div>
              )}
              <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)', marginTop: 8, lineHeight: 1.55 }}>
                {res.confidence === 'low'
                  ? 'Low confidence: the top pick didn’t hold across the clips (or the recipes disagreed on what was said). Treat it as a hint and run a few more files.'
                  : `Based on ${nclips} strata clip${nclips === 1 ? '' : 's'} (dialogue + a quiet stretch). The judge is strong at catching failures (loops, hallucination, dropout) and a rough guide to accuracy — a config that wins across clips is the safe bet. One-click adopt is coming.`}
                {res.agreement_mean != null && <> Recipes agreed {(res.agreement_mean * 100).toFixed(0)}% on what was said.</>}
              </div>
            </div>

            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--text-base)' }}>
                <thead>
                  <tr style={{ textAlign: 'left', color: 'var(--fg-3)' }}>
                    <th style={thStyle}>#</th><th style={thStyle}>Recipe</th>
                    {['Mean score', 'Faithfulness', 'Clips won', 'Issues'].map((c) => <th key={c} style={thStyle} title={COL_HELP[c]}>{c}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => {
                    const isWin = r.label === winner;
                    return (
                      <tr key={r.label} style={{ borderTop: 'var(--border)', background: isWin ? 'rgba(52,211,153,0.08)' : undefined }}>
                        <td style={tdStyle}>{i + 1}</td>
                        <td style={{ ...tdStyle, fontWeight: isWin ? 700 : 500, color: 'var(--fg-0)' }}>
                          {isWin && <span title="top pick" style={{ marginRight: 6 }}>★</span>}{r.label}
                        </td>
                        <td style={tdStyle}>{r.mean_composite?.toFixed(1)}</td>
                        <td style={tdStyle}>{r.mean_qe != null ? r.mean_qe.toFixed(3) : '—'}</td>
                        <td style={tdStyle}>{r.clips_won}/{nclips}</td>
                        <td style={tdStyle}>{r.disqualified_in > 0 ? <span style={{ color: 'var(--error-500)' }}>{r.disqualified_in}</span> : '—'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* per-clip breakdown — did the winner hold, or flip? */}
            <div style={{ marginTop: 10, fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>
              <div style={{ marginBottom: 4, fontWeight: 600, color: 'var(--fg-2)' }}>Per-clip winners</div>
              {(res.per_clip || []).map((c, i) => (
                <span key={i} style={{ marginRight: 12 }}>
                  {c.kind}: <b style={{ color: c.winner === winner ? 'var(--success-500)' : 'var(--fg-1)' }}>{c.winner || '—'}</b>
                  {c.agreement != null && <span style={{ color: 'var(--fg-3)' }}> ({(c.agreement * 100).toFixed(0)}%)</span>}
                </span>
              ))}
              <div style={{ marginTop: 6 }}>Hover a column heading for what it means.</div>
            </div>
          </>
        );
      })() : run.status === 'done' ? (
        <div style={{ color: 'var(--fg-2)', fontSize: 'var(--text-sm)' }}>No usable results — every recipe was rejected or produced no subtitle. Try a clip with clearer dialogue, or set the spoken language.</div>
      ) : null}
    </div>
  );
}

// The sweeps queue/history — backend-backed, so it survives navigation AND
// restart. `loaded` distinguishes "still fetching" from "genuinely empty" so
// we never flash "no sweeps" over data that's about to arrive.
// [#26] Per-language "herd" view — how recipes perform grouped by the spoken
// language subarr detected (robust majority vote). The foundation for
// per-language recommendations + the federated tournament.
function ByLanguagePanel({ data }) {
  if (!data || !data.length) return null;
  return (
    <Collapsible label="By language" id="bylang" defaultOpen={false}>
      <Hint>How each recipe performs grouped by the spoken language subarr detected on the file. Fills in as you run more sweeps — this is the groundwork for per-language recommendations.</Hint>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {data.map((lang) => {
          const top = lang.recipes[0];
          return (
            // #143: each language is its own collapsible (state persisted), so the
            // panel stays scannable as languages accumulate. Collapsed label shows
            // the headline (counts + current top pick).
            <Collapsible key={lang.language} id={`bylang-${lang.language}`} defaultOpen={false}
              label={
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                  {lang.language
                    ? <LangTag value={lang.language} size={13} style={{ fontWeight: 700, color: 'var(--fg-0)' }} />
                    : <span style={{ fontWeight: 700, letterSpacing: 0.5, color: 'var(--fg-0)' }}>?</span>}
                  <span style={{ color: 'var(--fg-3)', fontSize: 'var(--text-sm)', fontWeight: 400 }}>
                    {lang.files} file{lang.files === 1 ? '' : 's'} · {lang.sweeps} sweep{lang.sweeps === 1 ? '' : 's'}{top ? ` · top: ${top.label}` : ''}
                  </span>
                </span>
              }>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                {lang.recipes.map((r, i) => (
                  <div key={r.label} style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 'var(--text-sm)' }}>
                    <span style={{ width: 18, color: 'var(--fg-3)', flex: 'none' }}>{i === 0 ? '★' : ''}</span>
                    <span style={{ flex: 1, minWidth: 0, color: i === 0 ? 'var(--fg-0)' : 'var(--fg-2)', fontWeight: i === 0 ? 600 : 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.label}</span>
                    <span style={{ flex: 'none', color: 'var(--fg-3)' }} title="files where this recipe won (each file's repeat sweeps consolidated; ties excluded)">{r.wins}/{r.files} won</span>
                    <span style={{ flex: 'none', width: 64, textAlign: 'right', color: 'var(--fg-2)' }} title="mean composite score across this language's sweeps">{r.mean_composite}</span>
                  </div>
                ))}
              </div>
            </Collapsible>
          );
        })}
      </div>
      <div style={{ marginTop: 8, fontSize: 'var(--text-sm)', color: 'var(--fg-3)' }}>Each file votes once (its repeat sweeps are averaged), so re-sweeping a file sharpens its estimate rather than skewing the count. One file per language is a hint, not a verdict — trust grows with more files.</div>
    </Collapsible>
  );
}

function SweepList({ runs, detail, expandedId, onToggle, onDelete, loaded }) {
  const basename = (p) => (p || '').split('/').pop() || p;
  if (!loaded) {
    return <SectionCard label="Sweeps"><div style={{ color: 'var(--fg-3)', fontSize: 'var(--text-base)' }}>Loading sweeps…</div></SectionCard>;
  }
  if (!runs.length) {
    return (
      <SectionCard label="Sweeps">
        <div style={{ color: 'var(--fg-2)', fontSize: 'var(--text-base)', lineHeight: 1.6 }}>
          No sweeps yet. Queue one above and it’ll appear here with live status — and guidance (a top pick, what to avoid, and a
          confidence read) once the judge has ranked it. Sweeps keep running in the background, so you can queue several and come back.
        </div>
      </SectionCard>
    );
  }
  return (
    <SectionCard label="Sweeps">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {runs.map((r) => {
          const open = expandedId === r.id;
          const active = r.status === 'running' || r.status === 'pending' || r.status === 'queued';
          return (
            <div key={r.id} style={{ border: 'var(--border)', borderRadius: 'var(--radius-lg)', overflow: 'hidden', background: 'var(--bg-2)' }}>
              <div style={{ display: 'flex', alignItems: 'center' }}>
                <button onClick={() => onToggle(r.id)} style={sweepRow}>
                  <StatusPill status={r.status} />
                  <span style={{ flex: 1, minWidth: 0, fontWeight: 600, color: 'var(--fg-0)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.media_path}>{basename(r.media_path)}</span>
                  {r.source_language && (
                    <span title="detected source language"
                          style={{ flex: 'none', fontWeight: 700,
                                   color: 'var(--fg-2)', background: 'var(--bg-4)', borderRadius: 4, padding: '1px 6px' }}>
                      <LangTag value={r.source_language} size={11} />
                    </span>
                  )}
                  {active ? (
                    <span style={{ flex: 'none', display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ width: 70, height: 6, borderRadius: 3, background: 'var(--bg-4)', overflow: 'hidden' }}>
                        <span style={{ display: 'block', height: '100%', width: `${r.steps_total ? Math.round((r.steps_done / r.steps_total) * 100) : 0}%`, background: 'var(--violet-500)' }} />
                      </span>
                      <span style={{ color: 'var(--fg-3)', fontSize: 'var(--text-sm)' }}>{r.steps_done}/{r.steps_total || '…'}</span>
                    </span>
                  ) : (
                    <span style={{ color: 'var(--fg-3)', fontSize: 'var(--text-sm)', flex: 'none' }}>{r.recipe_count} recipe{r.recipe_count === 1 ? '' : 's'}</span>
                  )}
                  {r.status === 'done' && r.tie && <span style={{ color: 'var(--fg-3)', fontSize: 'var(--text-sm)', flex: 'none' }} title="recipes performed about the same">≈ tie</span>}
                  {r.winner && !r.tie && <span style={{ color: 'var(--success-500)', fontSize: 'var(--text-sm)', flex: 'none', maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={`top pick: ${r.winner}`}>top: {r.winner}</span>}
                  {r.status === 'done' && r.confidence && !r.tie && <ConfChip conf={r.confidence} />}
                  <span style={{ color: 'var(--fg-3)', flex: 'none' }}>{open ? '▾' : '▸'}</span>
                </button>
                <button onClick={() => onDelete(r.id)} title="remove sweep" style={{ ...iconBtnStyle, border: 'none', background: 'transparent', marginRight: 8 }}>
                  <Glyph char={ICONS.close || '×'} size={13} />
                </button>
              </div>
              {open && <div style={{ padding: '0 14px 12px' }}><SweepDetail run={detail[r.id]} /></div>}
            </div>
          );
        })}
      </div>
    </SectionCard>
  );
}

export function ArenaPage() {
  const [runs, setRuns] = useState([]);
  const [detail, setDetail] = useState({});
  const [expandedId, setExpandedId] = useState(null);
  const [gate, setGate] = useState(null);
  const [notice, setNotice] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const [byLang, setByLang] = useState([]);   // [#26] per-language herd view

  useEffect(() => {
    let alive = true;
    fetch('/api/integrations/health').then((r) => r.json()).then((d) => {
      if (!alive) return;
      const ok = !!d?.subgen?.asr_arena;
      setGate(ok ? { ok: true } : { ok: false, reason: 'This subgen build can’t run the tuning lab yet (it needs subarr-subgen v4.10 or newer).', remedy: 'Upgrade your subgen image to ghcr.io/coaxk/subarr-subgen:latest.' });
    }).catch(() => {});
    return () => { alive = false; };
  }, []);

  const loadRuns = useCallback(() => fetch('/api/arena/runs').then((r) => r.json()).then((d) => { setRuns(d.runs || []); setLoaded(true); }).catch(() => {}), []);
  const loadDetail = useCallback((id) => fetch(`/api/arena/${id}`).then((r) => r.json()).then((d) => setDetail((prev) => ({ ...prev, [id]: d }))).catch(() => {}), []);

  // [#26] per-language herd view — reload only when a sweep COMPLETES (the set
  // of done runs changes), not on every poll tick.
  const loadByLanguage = useCallback(() => fetch('/api/arena/by-language').then((r) => r.json()).then((d) => setByLang(d.languages || [])).catch(() => {}), []);
  const doneCount = runs.filter((r) => r.status === 'done').length;
  useEffect(() => { loadByLanguage(); }, [doneCount, loadByLanguage]);

  // Load the sweeps list on mount — this is what makes the page survive
  // navigation (state lives in the backend, not just this component).
  useEffect(() => { loadRuns(); }, [loadRuns]);

  // Poll while anything is pending/running (house usePoller pattern); also
  // refresh the open sweep's detail so its table fills in live.
  useEffect(() => {
    const active = runs.some((r) => r.status === 'pending' || r.status === 'running' || r.status === 'queued');
    if (!active) return;
    const t = setInterval(() => { loadRuns(); if (expandedId) loadDetail(expandedId); }, 2500);
    return () => clearInterval(t);
  }, [runs, expandedId, loadRuns, loadDetail]);

  const onToggle = useCallback((id) => {
    setExpandedId((prev) => { const next = prev === id ? null : id; if (next) loadDetail(next); return next; });
  }, [loadDetail]);

  const onDelete = useCallback((id) => {
    fetch(`/api/arena/${id}`, { method: 'DELETE' }).then(() => {
      setExpandedId((prev) => (prev === id ? null : prev));
      loadRuns();
    }).catch(() => {});
  }, [loadRuns]);

  const onRun = useCallback(async (body) => {
    setNotice(null);
    try {
      const r = await fetch('/api/arena/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const data = await r.json();
      if (!r.ok) {
        const d = data?.detail;
        setNotice(typeof d === 'string' ? d : (d?.reason || 'Could not queue the sweep.'));
        return false;
      }
      await loadRuns();
      setExpandedId(data.id); loadDetail(data.id);   // auto-expand the new sweep
      return true;
    } catch (e) { setNotice(String(e)); return false; }
  }, [loadRuns, loadDetail]);

  return (
    <main className="main-canvas" style={{ padding: '22px 24px 22px', gap: 14, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
      <div>
        <h1 style={{ margin: 0, fontSize: 'var(--text-h1)', fontWeight: 600 }}>Tuning Lab</h1>
        <div style={{ marginTop: 4, fontSize: 'var(--text-sm)', color: 'var(--fg-2)', maxWidth: 720 }}>
          Find the Whisper settings that give the best subtitles for a file, by trialling a few against the live model and
          letting the tournament judge rank them. Runs over subgen’s transcription engine — no upload, nothing written to your library.
        </div>
      </div>
      <WhatThisIs />
      <KnobReference />
      <SweepForm onRun={onRun} gate={gate} />
      {notice && <div style={gateNoticeStyle}>{notice}</div>}
      <ByLanguagePanel data={byLang} />
      <SweepList runs={runs} detail={detail} expandedId={expandedId} onToggle={onToggle} onDelete={onDelete} loaded={loaded} />
    </main>
  );
}

// ── styles (home-hifi tokens) ────────────────────────────────────────────────
const cardStyle = { background: 'var(--bg-1)', border: 'var(--border)', borderRadius: 'var(--radius-lg)', padding: '16px 18px' };
const fieldStyle = { display: 'flex', flexDirection: 'column', gap: 6 };
const lblStyle = { fontSize: 'var(--text-base)', fontWeight: 600, color: 'var(--fg-1)' };
const inputStyle = { background: 'var(--bg-2)', border: 'var(--border)', borderRadius: 'var(--radius-md)', padding: '8px 10px', color: 'var(--fg-0)', fontSize: 'var(--text-md)', outline: 'none' };
const thStyle = { padding: '6px 8px', fontWeight: 600, fontSize: 'var(--text-sm)', cursor: 'help', whiteSpace: 'nowrap' };
const tdStyle = { padding: '8px 8px', color: 'var(--fg-1)' };
const stepNum = { width: 22, height: 22, borderRadius: 'var(--radius-pill)', background: 'var(--violet-500)', color: '#fff', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 'var(--text-sm)', fontWeight: 700, flex: 'none' };
const expectStyle = { marginTop: 14, fontSize: 'var(--text-sm)', color: 'var(--fg-2)', lineHeight: 1.6, background: 'var(--bg-2)', border: 'var(--border)', borderRadius: 'var(--radius-lg)', padding: '10px 12px' };
const primaryBtnStyle = { background: 'var(--success-500)', color: 'var(--bg-0)', border: 'none', borderRadius: 'var(--radius-md)', padding: '10px 18px', fontWeight: 700, fontSize: 'var(--text-md)' };
const ghostBtnStyle = { background: 'var(--bg-2)', color: 'var(--fg-1)', border: 'var(--border)', borderRadius: 'var(--radius-md)', padding: '8px 14px', fontSize: 'var(--text-base)', cursor: 'pointer', flex: 'none' };
const iconBtnStyle = { background: 'var(--bg-2)', color: 'var(--fg-2)', border: 'var(--border)', borderRadius: 'var(--radius-md)', padding: '8px 10px', cursor: 'pointer', flex: 'none' };
const crumbStyle = { background: 'transparent', border: 'none', color: 'var(--violet-400)', cursor: 'pointer', fontSize: 'var(--text-sm)', padding: 0 };
const codeName = { color: 'var(--violet-400)', fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)' };
const gateNoticeStyle = { background: 'rgba(245,158,11,0.10)', border: '1px solid var(--warn-500)', borderRadius: 'var(--radius-lg)', padding: '10px 12px', fontSize: 'var(--text-base)', color: 'var(--fg-1)' };
const guidanceBoxStyle = { background: 'var(--bg-2)', border: 'var(--border)', borderRadius: 'var(--radius-lg)', padding: '12px 14px', marginBottom: 14 };
const explainStyle = { background: 'rgba(139,92,246,0.10)', border: '1px solid var(--violet-500)', borderRadius: 'var(--radius-lg)', padding: '10px 12px', marginBottom: 10, fontSize: 'var(--text-sm)', color: 'var(--fg-1)', lineHeight: 1.55 };
const winnerCode = { display: 'block', marginTop: 6, fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)', background: 'var(--bg-0)', border: 'var(--border)', padding: '8px 10px', borderRadius: 'var(--radius-lg)', overflowX: 'auto', color: 'var(--fg-1)' };
const sweepRow = { display: 'flex', alignItems: 'center', gap: 12, flex: 1, minWidth: 0, textAlign: 'left', background: 'transparent', border: 'none', cursor: 'pointer', padding: '12px 14px', color: 'var(--fg-1)' };
const progRow = { display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--text-base)' };
const modalBackdrop = { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 };
const modalCard = { background: 'var(--bg-1)', border: 'var(--border-strong)', borderRadius: 'var(--radius-lg)', padding: 18, width: 'min(640px, 92vw)', boxShadow: 'var(--shadow-modal)' };
const pickRow = { display: 'block', width: '100%', textAlign: 'left', background: 'transparent', border: 'none', borderBottom: 'var(--border)', color: 'var(--fg-1)', padding: '10px 12px', cursor: 'pointer', fontSize: 'var(--text-base)' };
const pickRowMuted = { padding: '16px 12px', color: 'var(--fg-3)', fontSize: 'var(--text-base)' };
