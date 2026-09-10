// #479: the wizard now tests a connection by itself once the fields settle,
// and a failed test changes what Continue says. Both decisions are pure and
// pinned here; the debounce and rendering are covered by the jsdom test.
import { describe, it, expect } from 'vitest';
import { readyToTest, continueLabelFor } from '../onboarding-test-policy.mjs';

describe('readyToTest', () => {
  it('a URL-only service is ready as soon as the URL looks like one', () => {
    expect(readyToTest('subgen', { subgen_url: 'http://subgen:9000' })).toBe(true);
    expect(readyToTest('ollama', { ollama_url: 'http://ollama:11434' })).toBe(true);
  });

  it('is not ready on an empty, partial, or scheme-less URL', () => {
    expect(readyToTest('subgen', {})).toBe(false);
    expect(readyToTest('subgen', { subgen_url: 'http://' })).toBe(false);
    expect(readyToTest('subgen', { subgen_url: 'subgen:9000' })).toBe(false);
    expect(readyToTest('subgen', { subgen_url: 'http://s' })).toBe(true);
  });

  it('a keyed service also needs its API key', () => {
    expect(readyToTest('sonarr', { sonarr_url: 'http://sonarr:8989' })).toBe(false);
    expect(readyToTest('sonarr', { sonarr_url: 'http://sonarr:8989', sonarr_api_key: 'k' })).toBe(true);
  });
});

describe('continueLabelFor', () => {
  it('reads Continue normally and Finish on the last step', () => {
    expect(continueLabelFor({ isLast: false, testResult: null })).toBe('Continue →');
    expect(continueLabelFor({ isLast: true, testResult: null })).toBe('Finish setup →');
    expect(continueLabelFor({ isLast: false, testResult: { ok: true } })).toBe('Continue →');
  });

  it('says "anyway" when the last test of this step failed, so skipping past a dead URL is a visible choice', () => {
    expect(continueLabelFor({ isLast: false, testResult: { ok: false } })).toBe('Continue anyway →');
    expect(continueLabelFor({ isLast: true, testResult: { ok: false } })).toBe('Finish anyway →');
  });
});
