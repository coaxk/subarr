// #201: first frontend unit tests. Pin the pure helpers in atoms.jsx —
// language normalization is core (the "FR FR" / mislabelled-language bug
// territory) and ran with zero coverage until now.
import { describe, it, expect } from 'vitest';
import { normalizeLang, langName, fmtTime, genSpark } from '../atoms.jsx';

describe('normalizeLang', () => {
  it('passes through canonical ISO-639-1 codes', () => {
    expect(normalizeLang('en')).toBe('en');
    expect(normalizeLang('fr')).toBe('fr');
    expect(normalizeLang('ja')).toBe('ja');
  });

  it('is case- and whitespace-insensitive', () => {
    expect(normalizeLang('  EN ')).toBe('en');
    expect(normalizeLang('Fr')).toBe('fr');
  });

  it('resolves 3-letter and name aliases to ISO-639-1', () => {
    expect(normalizeLang('eng')).toBe('en');
    expect(normalizeLang('english')).toBe('en');
    expect(normalizeLang('jpn')).toBe('ja');
    expect(normalizeLang('french')).toBe('fr');
  });

  it('returns empty string for falsy input (no crash)', () => {
    expect(normalizeLang('')).toBe('');
    expect(normalizeLang(null)).toBe('');
    expect(normalizeLang(undefined)).toBe('');
  });

  it('passes an unknown 2-letter code through unchanged', () => {
    expect(normalizeLang('zz')).toBe('zz');
  });
});

describe('langName', () => {
  it('maps codes and aliases to the language name', () => {
    expect(langName('en')).toBe('English');
    expect(langName('ja')).toBe('Japanese');
    expect(langName('jpn')).toBe('Japanese');
    expect(langName('french')).toBe('French');
  });

  it('uppercases an unknown value rather than inventing a name', () => {
    expect(langName('zz')).toBe('ZZ');
  });

  it('returns empty string for empty input', () => {
    expect(langName('')).toBe('');
    expect(langName(null)).toBe('');
  });
});

describe('fmtTime', () => {
  it('zero-pads h:m:s', () => {
    const d = new Date(2026, 5, 16, 9, 5, 3);
    expect(fmtTime(d)).toBe('09:05:03');
  });

  it('renders a late time without padding artifacts', () => {
    const d = new Date(2026, 5, 16, 23, 59, 59);
    expect(fmtTime(d)).toBe('23:59:59');
  });
});

describe('genSpark', () => {
  it('produces exactly n points, all non-negative', () => {
    const pts = genSpark(24, 10, 4);
    expect(pts).toHaveLength(24);
    expect(pts.every((v) => v >= 0)).toBe(true);
  });

  it('returns an empty array for zero length', () => {
    expect(genSpark(0, 10, 4)).toEqual([]);
  });
});
