// #378: Library Browse root picker — pure helpers (node-env, no rendering).
import { describe, it, expect } from 'vitest';
import { browseQuery, rootPathForLibrary, libraryPickerOptions } from '../library.jsx';

describe('rootPathForLibrary', () => {
  it('maps the default library (empty slug) to the empty root path', () => {
    expect(rootPathForLibrary('')).toBe('');
    expect(rootPathForLibrary(null)).toBe('');
    expect(rootPathForLibrary(undefined)).toBe('');
  });

  it('maps a non-default slug to its @slug/ canonical root head', () => {
    expect(rootPathForLibrary('anime')).toBe('@anime/');
    expect(rootPathForLibrary('kids-movies')).toBe('@kids-movies/');
  });
});

describe('browseQuery', () => {
  it('skips rollup at the default root (no path)', () => {
    expect(browseQuery('', true)).toBe('?rollup=false');
  });

  it('skips rollup at a non-default library root but still passes its path', () => {
    expect(browseQuery('@anime/', true)).toBe('?path=%40anime%2F&rollup=false');
  });

  it('passes the path with rollup ON when drilling into a subtree', () => {
    expect(browseQuery('@anime/Frieren', false)).toBe('?path=%40anime%2FFrieren');
    expect(browseQuery('TV/Show', false)).toBe('?path=TV%2FShow');
  });
});

describe('libraryPickerOptions', () => {
  const libs = [
    { slug: '', name: 'TV', is_default: true },
    { slug: 'anime', name: 'Anime', is_default: false },
    { slug: 'movies', name: 'Movies', is_default: false },
  ];

  it('returns null when there is only the default library (single-stack: no picker)', () => {
    expect(libraryPickerOptions([{ slug: '', name: 'TV', is_default: true }])).toBe(null);
    expect(libraryPickerOptions([])).toBe(null);
    expect(libraryPickerOptions(null)).toBe(null);
  });

  it('lists default first then the rest by name when >1 library', () => {
    const opts = libraryPickerOptions(libs);
    expect(opts.map((o) => o.slug)).toEqual(['', 'anime', 'movies']);
    expect(opts[0].name).toBe('TV');
  });
});
