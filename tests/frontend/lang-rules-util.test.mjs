import assert from 'node:assert/strict';
import {
  distinctSeriesPrefixes, deriveTitle, ladderLetterFor,
  groupRulesAlphabetically, activeLadderLetters,
} from '../../src/subarr/static/v1/home-hifi/lang-rules-util.mjs';

// distinctSeriesPrefixes: map selected file paths -> distinct series roots (+ '/')
const items = [
  { file_canonical_path: 'TV/Cheers/Season 1/e1.mkv', canonical_path: 'TV/Cheers' },
  { file_canonical_path: 'TV/Cheers/Season 1/e2.mkv', canonical_path: 'TV/Cheers' },
  { file_canonical_path: 'Movies/Parasite (2019)/p.mkv', canonical_path: 'Movies/Parasite (2019)' },
];
const prefixes = distinctSeriesPrefixes(
  ['TV/Cheers/Season 1/e1.mkv', 'TV/Cheers/Season 1/e2.mkv', 'Movies/Parasite (2019)/p.mkv'],
  items,
).sort();
assert.deepEqual(prefixes, ['Movies/Parasite (2019)/', 'TV/Cheers/']);

// falls back to the path itself (slash-terminated) when not found in items
assert.deepEqual(distinctSeriesPrefixes(['TV/Unknown/x.mkv'], []), ['TV/Unknown/x.mkv/']);

// deriveTitle: last non-empty segment of the prefix
assert.equal(deriveTitle('TV/Cheers/'), 'Cheers');
assert.equal(deriveTitle('Movies/Parasite (2019)/'), 'Parasite (2019)');

// ladderLetterFor: A-Z uppercase, non-alpha -> '#'
assert.equal(ladderLetterFor('Cheers'), 'C');
assert.equal(ladderLetterFor('300'), '#');

// groupRulesAlphabetically: sections sorted, rules sorted by title within
const rules = [
  { series_prefix: 'TV/Squid Game/', lang_code: 'kor' },
  { series_prefix: 'TV/Cheers/', lang_code: 'eng' },
  { series_prefix: 'Movies/Parasite (2019)/', lang_code: 'kor' },
];
const groups = groupRulesAlphabetically(rules);
assert.deepEqual(groups.map(g => g.letter), ['C', 'P', 'S']);
assert.equal(groups[0].rules[0].title, 'Cheers');

// activeLadderLetters: set of present first-letters
const active = activeLadderLetters(rules);
assert.ok(active.has('C') && active.has('P') && active.has('S'));
assert.ok(!active.has('A'));

console.log('lang-rules-util: all assertions passed');
