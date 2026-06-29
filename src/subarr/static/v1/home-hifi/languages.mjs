// #358: single front-end source for the audio-language picker. Fetches the full
// Whisper set from GET /api/languages once and caches it (the set never changes
// within a session). Returns [code, name] pairs to match the old LANG_PICKS shape.
let _cache = null;
let _inflight = null;

export function _resetLanguagesCache() {
  _cache = null;
  _inflight = null;
}

export async function fetchLanguages() {
  if (_cache) return _cache;
  if (_inflight) return _inflight;
  _inflight = (async () => {
    const r = await fetch('/api/languages');
    if (!r.ok) throw new Error(`/api/languages ${r.status}`);
    const data = await r.json();
    _cache = (data.languages || []).map((l) => [l.code, l.name]);
    _inflight = null;
    return _cache;
  })();
  return _inflight;
}

// React hook: returns the [code, name] pairs, loading them once on mount.
// References the global React lazily (inside the hook) so this module stays
// importable in non-React contexts (e.g. the fetchLanguages unit test).
export function useLanguagePicks() {
  const { useState, useEffect } = React;
  const [picks, setPicks] = useState(_cache || []);
  useEffect(() => {
    let alive = true;
    fetchLanguages()
      .then((p) => {
        if (alive) setPicks(p);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);
  return picks;
}
