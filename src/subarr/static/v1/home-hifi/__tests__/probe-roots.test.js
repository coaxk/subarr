// #546: probe roots that do not exist failed every scheduled walk silently, and
// the wizard offered `TV, Movies` whether or not those folders existed (#524).
import { describe, it, expect } from 'vitest';
import { missingRootsMessage, initialRootsText } from '../probe-roots.mjs';

describe('missingRootsMessage', () => {
  it('is empty when every root resolves', () => {
    expect(missingRootsMessage([{ root: 'Film', ok: true }])).toBe('');
  });

  it('names each missing root and says walks skip it', () => {
    const msg = missingRootsMessage([
      { root: 'Film', ok: true },
      { root: 'TV', ok: false, reason: 'root not found: TV' },
      { root: 'Movies', ok: false, reason: 'root not found: Movies' },
    ]);
    expect(msg).toMatch(/TV/);
    expect(msg).toMatch(/Movies/);
    expect(msg).not.toMatch(/Film/);
    expect(msg).toMatch(/not found/i);
  });

  it('tolerates a missing or malformed check', () => {
    expect(missingRootsMessage(undefined)).toBe('');
    expect(missingRootsMessage(null)).toBe('');
    expect(missingRootsMessage([{ root: 'x' }])).toBe('');
  });
});

describe('initialRootsText', () => {
  it('keeps what the user already chose', () => {
    expect(initialRootsText(['Film', 'Serie Tv'], ['Anything'])).toBe('Film, Serie Tv');
  });

  it('uses the folders that exist when nothing was chosen', () => {
    expect(initialRootsText([], ['Film', 'Serie Tv'])).toBe('Film, Serie Tv');
    expect(initialRootsText(undefined, ['Film'])).toBe('Film');
  });

  it('never invents TV, Movies', () => {
    expect(initialRootsText([], [])).toBe('');
    expect(initialRootsText(undefined, undefined)).toBe('');
  });
});
