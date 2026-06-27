// #161 Phase 4C: coverage library-filter options (node-env, no rendering).
import { describe, it, expect } from 'vitest';
import { libraryOptions } from '../coverage.jsx';

describe('libraryOptions', () => {
  it('returns just "All" when every row is the default library (no slug)', () => {
    const opts = libraryOptions([{ library: { slug: '', name: 'default' } }, { library: null }]);
    expect(opts).toEqual([{ slug: 'all', name: 'All libraries' }]);
  });

  it('lists distinct non-default libraries, sorted by name, after All', () => {
    const opts = libraryOptions([
      { library: { slug: 'tv', name: 'TV' } },
      { library: { slug: 'anime', name: 'Anime' } },
      { library: { slug: 'tv', name: 'TV' } }, // dup
      { library: { slug: '', name: 'default' } },
    ]);
    expect(opts).toEqual([
      { slug: 'all', name: 'All libraries' },
      { slug: 'anime', name: 'Anime' },
      { slug: 'tv', name: 'TV' },
    ]);
  });

  it('handles null/empty input', () => {
    expect(libraryOptions(null)).toEqual([{ slug: 'all', name: 'All libraries' }]);
  });
});
