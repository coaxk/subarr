// Pure helpers for the series/title audio-language "intent" UI. No React/JSX
// here so the logic is unit-testable with plain `node` (see
// tests/frontend/lang-rules-util.test.mjs). Imported by review.jsx + settings.jsx.

// Map a set of selected file canonical paths to the DISTINCT series/movie
// prefixes they belong to (each slash-terminated, matching the store's
// series_prefix convention). The series root comes from each item's
// canonical_path; if a path isn't found in `items`, fall back to the path
// itself so we never silently drop a selection.
export function distinctSeriesPrefixes(selectedPaths, items) {
  const rootByFile = new Map();
  for (const it of items || []) {
    const file = it.file_canonical_path || it.canonical_path;
    const root = it.canonical_path || it.file_canonical_path;
    if (file && root) rootByFile.set(file, root);
  }
  const out = new Set();
  for (const p of selectedPaths || []) {
    const root = rootByFile.get(p) || p;
    out.add(root.endsWith('/') ? root : root + '/');
  }
  return Array.from(out);
}

// Last non-empty path segment of a prefix, e.g. "TV/Cheers/" -> "Cheers".
export function deriveTitle(prefix) {
  const parts = String(prefix || '').split('/').filter(Boolean);
  return parts.length ? parts[parts.length - 1] : String(prefix || '');
}

// A-Z ladder bucket for a title: uppercase first letter, or '#' for non-alpha.
export function ladderLetterFor(title) {
  const c = String(title || '').trim().charAt(0).toUpperCase();
  return c >= 'A' && c <= 'Z' ? c : '#';
}

// Group rules into alphabetical sections by derived title. Each rule is
// annotated with a derived `title`. Sections sorted by letter; rules sorted by
// title within each section. Returns [{ letter, rules: [{...rule, title}] }].
export function groupRulesAlphabetically(rules) {
  const withTitle = (rules || []).map((r) => ({ ...r, title: deriveTitle(r.series_prefix) }));
  const byLetter = new Map();
  for (const r of withTitle) {
    const letter = ladderLetterFor(r.title);
    if (!byLetter.has(letter)) byLetter.set(letter, []);
    byLetter.get(letter).push(r);
  }
  const letters = Array.from(byLetter.keys()).sort();
  return letters.map((letter) => ({
    letter,
    rules: byLetter.get(letter).sort((a, b) =>
      a.title.localeCompare(b.title, undefined, { sensitivity: 'base' })),
  }));
}

// Set of ladder letters that have at least one rule (for highlighting the
// right-hand A-Z ladder).
export function activeLadderLetters(rules) {
  const set = new Set();
  for (const r of rules || []) set.add(ladderLetterFor(deriveTitle(r.series_prefix)));
  return set;
}
