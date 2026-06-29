// #358: the picker's language source — fetch once, cache, sorted full set.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fetchLanguages, _resetLanguagesCache } from '../languages.mjs';

describe('fetchLanguages', () => {
  beforeEach(() => {
    _resetLanguagesCache();
  });

  it('fetches /api/languages and returns [code,name] pairs', async () => {
    global.fetch = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        languages: [
          { code: 'gl', name: 'Galician' },
          { code: 'ko', name: 'Korean' },
        ],
      }),
    }));
    const pairs = await fetchLanguages();
    expect(pairs).toEqual([
      ['gl', 'Galician'],
      ['ko', 'Korean'],
    ]);
  });

  it('caches — second call does not re-fetch', async () => {
    const spy = vi.fn(async () => ({
      ok: true,
      json: async () => ({ languages: [{ code: 'en', name: 'English' }] }),
    }));
    global.fetch = spy;
    await fetchLanguages();
    await fetchLanguages();
    expect(spy).toHaveBeenCalledTimes(1);
  });
});
